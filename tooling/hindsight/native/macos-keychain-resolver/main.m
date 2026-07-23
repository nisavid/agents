#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <pwd.h>
#import <signal.h>
#import <unistd.h>

static const NSUInteger MaxRequestBytes = 64 * 1024;

static NSString *DecodeSecret(NSData *data);

static int Fail(NSString *message) {
    fprintf(stderr, "%s\n", message.UTF8String);
    return 1;
}

static NSString *AccountName(void) {
    struct passwd *entry = getpwuid(getuid());
    if (entry == NULL || entry->pw_name == NULL) {
        return nil;
    }
    return [NSString stringWithUTF8String:entry->pw_name];
}

static NSDictionary<NSString *, NSDictionary<NSString *, NSString *> *> *
Bindings(void) {
    return @{
        @"HINDSIGHT_DATA_PLANE_TOKEN": @{
            @"locator": @"keychain://io.nisavid.hindsight/data-plane",
            @"service": @"io.nisavid.hindsight.data-plane",
        },
        @"HINDSIGHT_MINT_AUTHORITY": @{
            @"locator": @"keychain://io.nisavid.hindsight/mint-authority",
            @"service": @"io.nisavid.hindsight.mint-authority",
        },
        @"HINDSIGHT_UI_ACCESS_KEY": @{
            @"locator": @"keychain://io.nisavid.hindsight/ui-access-key",
            @"service": @"io.nisavid.hindsight.ui-access-key",
        },
    };
}

static BOOL HasExactKeys(NSDictionary *value, NSArray<NSString *> *keys) {
    return [value isKindOfClass:NSDictionary.class]
        && [NSSet setWithArray:value.allKeys].count == keys.count
        && [[NSSet setWithArray:value.allKeys]
            isEqualToSet:[NSSet setWithArray:keys]];
}

static BOOL IsSecret(NSString *value) {
    if (![value isKindOfClass:NSString.class]
        || value.length < 32
        || value.length > 4096) {
        return NO;
    }
    static NSCharacterSet *invalid;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        invalid = [[NSCharacterSet
            characterSetWithCharactersInString:
                @"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "abcdefghijklmnopqrstuvwxyz"
                 "0123456789._~+/=-"]
            invertedSet];
    });
    return [value rangeOfCharacterFromSet:invalid].location == NSNotFound;
}

static NSData *ReadRequest(void) {
    NSFileHandle *input = NSFileHandle.fileHandleWithStandardInput;
    NSMutableData *result = [NSMutableData data];
    while (YES) {
        NSData *chunk = [input readDataOfLength:4096];
        if (chunk.length == 0) {
            return result;
        }
        if (result.length + chunk.length > MaxRequestBytes) {
            return nil;
        }
        [result appendData:chunk];
    }
}

static NSArray<NSDictionary<NSString *, NSString *> *> *
ParseRequest(NSData *raw, NSString **errorMessage) {
    if (raw == nil || raw.length == 0) {
        *errorMessage = @"credential request is invalid";
        return nil;
    }
    NSError *error = nil;
    id parsed = [NSJSONSerialization JSONObjectWithData:raw options:0 error:&error];
    if (error != nil
        || !HasExactKeys(parsed, @[@"credentials", @"schema_version"])) {
        *errorMessage = @"credential request schema is closed";
        return nil;
    }
    NSDictionary *request = parsed;
    NSNumber *version = request[@"schema_version"];
    NSArray *credentials = request[@"credentials"];
    if (![version isKindOfClass:NSNumber.class]
        || CFGetTypeID((__bridge CFTypeRef)version) == CFBooleanGetTypeID()
        || version.integerValue != 1
        || version.doubleValue != 1.0
        || ![credentials isKindOfClass:NSArray.class]
        || credentials.count == 0
        || credentials.count > Bindings().count) {
        *errorMessage = @"credential request is invalid";
        return nil;
    }

    NSMutableSet<NSString *> *environments = [NSMutableSet set];
    for (id candidate in credentials) {
        if (!HasExactKeys(candidate, @[@"environment", @"locator"])) {
            *errorMessage = @"credential binding schema is closed";
            return nil;
        }
        NSDictionary *item = candidate;
        NSString *environment = item[@"environment"];
        NSString *locator = item[@"locator"];
        NSDictionary *binding = Bindings()[environment];
        if (![environment isKindOfClass:NSString.class]
            || ![locator isKindOfClass:NSString.class]
            || binding == nil
            || ![locator isEqualToString:binding[@"locator"]]
            || [environments containsObject:environment]) {
            *errorMessage = @"credential binding is not authorized";
            return nil;
        }
        [environments addObject:environment];
    }

    NSData *canonical = [NSJSONSerialization
        dataWithJSONObject:request
        options:NSJSONWritingSortedKeys
        error:&error];
    NSMutableString *canonicalText = [[[NSString alloc]
        initWithData:canonical
        encoding:NSUTF8StringEncoding] mutableCopy];
    [canonicalText
        replaceOccurrencesOfString:@"\\/"
        withString:@"/"
        options:0
        range:NSMakeRange(0, canonicalText.length)];
    canonical = [canonicalText dataUsingEncoding:NSUTF8StringEncoding];
    if (error != nil || canonical == nil || ![canonical isEqualToData:raw]) {
        *errorMessage = @"credential request is not canonical";
        return nil;
    }
    return credentials;
}

static NSMutableDictionary *Query(NSString *service, NSString *account) {
    return [@{
        (__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService: service,
        (__bridge id)kSecAttrAccount: account,
    } mutableCopy];
}

static OSStatus CopySecretData(
    SecKeychainItemRef item,
    NSData **secret
) {
    UInt32 length = 0;
    void *bytes = NULL;
    OSStatus status = SecKeychainItemCopyContent(
        item,
        NULL,
        NULL,
        &length,
        &bytes
    );
    if (status != errSecSuccess) {
        if (bytes != NULL) {
            SecKeychainItemFreeContent(NULL, bytes);
        }
        return status;
    }
    if (bytes == NULL || length == 0) {
        if (bytes != NULL) {
            SecKeychainItemFreeContent(NULL, bytes);
        }
        return errSecInternalError;
    }
    *secret = [NSData dataWithBytes:bytes length:length];
    OSStatus freeStatus = SecKeychainItemFreeContent(NULL, bytes);
    if (freeStatus != errSecSuccess) {
        *secret = nil;
        return freeStatus;
    }
    return errSecSuccess;
}

static SecTrustedApplicationRef CreateTrustedApplication(
    NSString *executablePath
) {
    SecTrustedApplicationRef result = NULL;
    if (SecTrustedApplicationCreateFromPath(
            executablePath.fileSystemRepresentation,
            &result
        ) != errSecSuccess) {
        if (result != NULL) {
            CFRelease(result);
        }
        return NULL;
    }
    return result;
}

static SecAccessRef CreateAccess(NSArray<NSString *> *executablePaths) {
    NSMutableArray *trusted = [NSMutableArray array];
    for (NSString *path in executablePaths) {
        SecTrustedApplicationRef application = CreateTrustedApplication(path);
        if (application == NULL) {
            return NULL;
        }
        [trusted addObject:(__bridge id)application];
        CFRelease(application);
    }
    if (trusted.count != executablePaths.count) {
        return NULL;
    }
    SecAccessRef access = NULL;
    OSStatus status = SecAccessCreate(
        CFSTR("Hindsight credential resolver"),
        (__bridge CFArrayRef)trusted,
        &access
    );
    if (status != errSecSuccess) {
        if (access != NULL) {
            CFRelease(access);
        }
        return NULL;
    }
    return access;
}

static OSStatus CopyItem(
    NSString *service,
    NSString *account,
    SecKeychainItemRef *item
) {
    NSMutableDictionary *query = Query(service, account);
    query[(__bridge id)kSecReturnRef] = @YES;
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef result = NULL;
    OSStatus status = SecItemCopyMatching(
        (__bridge CFDictionaryRef)query,
        &result
    );
    if (status == errSecSuccess) {
        if (result == NULL
            || CFGetTypeID(result) != SecKeychainItemGetTypeID()) {
            if (result != NULL) {
                CFRelease(result);
            }
            return errSecInternalError;
        }
        *item = (SecKeychainItemRef)result;
    }
    return status;
}

static BOOL TrustedApplicationMatches(
    SecTrustedApplicationRef observed,
    SecTrustedApplicationRef expected
) {
    CFDataRef observedData = NULL;
    CFDataRef expectedData = NULL;
    OSStatus observedStatus = SecTrustedApplicationCopyData(
        observed,
        &observedData
    );
    OSStatus expectedStatus = SecTrustedApplicationCopyData(
        expected,
        &expectedData
    );
    BOOL matches = observedStatus == errSecSuccess
        && expectedStatus == errSecSuccess
        && observedData != NULL
        && expectedData != NULL
        && CFEqual(observedData, expectedData);
    if (observedData != NULL) {
        CFRelease(observedData);
    }
    if (expectedData != NULL) {
        CFRelease(expectedData);
    }
    return matches;
}

static BOOL ItemHasExactReadACL(
    SecKeychainItemRef item,
    NSString *executablePath
) {
    SecAccessRef access = NULL;
    if (SecKeychainItemCopyAccess(item, &access) != errSecSuccess
        || access == NULL) {
        return NO;
    }
    CFArrayRef lists = SecAccessCopyMatchingACLList(
        access,
        kSecACLAuthorizationDecrypt
    );
    CFArrayRef anyLists = SecAccessCopyMatchingACLList(
        access,
        kSecACLAuthorizationAny
    );
    BOOL hasAnyAuthorization = anyLists != NULL
        && CFArrayGetCount(anyLists) > 0;
    if (anyLists != NULL) {
        CFRelease(anyLists);
    }
    if (hasAnyAuthorization
        || lists == NULL
        || CFArrayGetCount(lists) != 1) {
        if (lists != NULL) {
            CFRelease(lists);
        }
        CFRelease(access);
        return NO;
    }

    CFArrayRef applications = NULL;
    CFStringRef description = NULL;
    SecKeychainPromptSelector prompt = {0};
    OSStatus status = SecACLCopyContents(
        (SecACLRef)CFArrayGetValueAtIndex(lists, 0),
        &applications,
        &description,
        &prompt
    );
    CFRelease(lists);
    if (description != NULL) {
        CFRelease(description);
    }
    if (status != errSecSuccess
        || applications == NULL
        || CFArrayGetCount(applications) != 1) {
        if (applications != NULL) {
            CFRelease(applications);
        }
        CFRelease(access);
        return NO;
    }

    SecTrustedApplicationRef expected = CreateTrustedApplication(
        executablePath
    );
    BOOL matches = expected != NULL
        && TrustedApplicationMatches(
            (SecTrustedApplicationRef)CFArrayGetValueAtIndex(applications, 0),
            expected
        );
    if (expected != NULL) {
        CFRelease(expected);
    }
    CFRelease(applications);
    CFRelease(access);
    return matches;
}

static OSStatus CopyProtectedSecret(
    NSString *service,
    NSString *account,
    NSString *executablePath,
    NSData **secret
) {
    SecKeychainItemRef item = NULL;
    OSStatus status = CopyItem(service, account, &item);
    if (status != errSecSuccess) {
        return status;
    }
    BOOL protected = ItemHasExactReadACL(item, executablePath);
    if (!protected) {
        CFRelease(item);
        return errSecAuthFailed;
    }
    status = CopySecretData(item, secret);
    CFRelease(item);
    return status;
}

static OSStatus CreateSecretWithAccess(
    NSString *service,
    NSString *account,
    NSString *value,
    SecAccessRef access
) {
    NSMutableDictionary *attributes = Query(service, account);
    attributes[(__bridge id)kSecValueData] =
        [value dataUsingEncoding:NSUTF8StringEncoding];
    attributes[(__bridge id)kSecAttrAccess] = (__bridge id)access;
    OSStatus status = SecItemAdd(
        (__bridge CFDictionaryRef)attributes,
        NULL
    );
    return status;
}

static OSStatus CreateSecret(
    NSString *service,
    NSString *account,
    NSString *value,
    NSArray<NSString *> *trustedPaths
) {
    SecAccessRef access = CreateAccess(trustedPaths);
    if (access == NULL) {
        return errSecInternalError;
    }
    OSStatus status = CreateSecretWithAccess(
        service,
        account,
        value,
        access
    );
    CFRelease(access);
    return status;
}

static OSStatus CreateSecretWithSeparateAnyACL(
    NSString *service,
    NSString *account,
    NSString *value,
    NSString *executablePath
) {
    SecAccessRef access = CreateAccess(@[executablePath]);
    if (access == NULL) {
        return errSecInternalError;
    }

    SecACLRef acl = NULL;
    OSStatus status = SecACLCreateWithSimpleContents(
        access,
        NULL,
        CFSTR("Hindsight credential resolver self-test"),
        0,
        &acl
    );
    if (status == errSecSuccess && acl != NULL) {
        status = SecACLUpdateAuthorizations(
            acl,
            (__bridge CFArrayRef)@[
                (__bridge id)kSecACLAuthorizationAny,
            ]
        );
    }
    if (status == errSecSuccess) {
        status = CreateSecretWithAccess(
            service,
            account,
            value,
            access
        );
    }
    if (acl != NULL) {
        CFRelease(acl);
    }
    CFRelease(access);
    return status;
}

static OSStatus AddSecret(
    NSString *service,
    NSString *account,
    NSString *value,
    NSString *executablePath
) {
    NSData *existing = nil;
    OSStatus status = CopyProtectedSecret(
        service,
        account,
        executablePath,
        &existing
    );
    if (status == errSecSuccess) {
        return DecodeSecret(existing) == nil
            ? errSecDecode
            : errSecSuccess;
    }
    if (status != errSecItemNotFound) {
        return status;
    }
    return CreateSecret(
        service,
        account,
        value,
        @[executablePath]
    );
}

static NSString *RandomSecret(void) {
    uint8_t bytes[48];
    if (SecRandomCopyBytes(kSecRandomDefault, sizeof(bytes), bytes)
        != errSecSuccess) {
        return nil;
    }
    NSData *data = [NSData dataWithBytes:bytes length:sizeof(bytes)];
    return [data base64EncodedStringWithOptions:0];
}

static NSString *DecodeSecret(NSData *data) {
    NSString *value = [[NSString alloc]
        initWithData:data
        encoding:NSUTF8StringEncoding];
    return IsSecret(value) ? value : nil;
}

static BOOL DeleteSecret(NSString *service, NSString *account) {
    OSStatus status = SecItemDelete(
        (__bridge CFDictionaryRef)Query(service, account)
    );
    return status == errSecSuccess || status == errSecItemNotFound;
}

static OSStatus RetireSecret(
    NSString *service,
    NSString *account,
    NSString *executablePath
) {
    SecKeychainItemRef item = NULL;
    OSStatus status = CopyItem(service, account, &item);
    if (status == errSecItemNotFound) {
        return errSecSuccess;
    }
    if (status != errSecSuccess) {
        return status;
    }
    if (!ItemHasExactReadACL(item, executablePath)) {
        CFRelease(item);
        return errSecAuthFailed;
    }
    status = SecKeychainItemDelete(item);
    CFRelease(item);
    return status == errSecItemNotFound ? errSecSuccess : status;
}

static BOOL PythonReturnsStatus(
    NSString *service,
    NSString *account,
    OSStatus expectedStatus
) {
    NSString *program =
        @"import ctypes,signal,sys\n"
         "signal.alarm(4)\n"
         "security=ctypes.CDLL("
         "'/System/Library/Frameworks/Security.framework/Security')\n"
         "core=ctypes.CDLL("
         "'/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')\n"
         "security.SecKeychainSetUserInteractionAllowed.argtypes=[ctypes.c_bool]\n"
         "security.SecKeychainSetUserInteractionAllowed.restype=ctypes.c_int32\n"
         "security.SecKeychainFindGenericPassword.argtypes=["
         "ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,"
         "ctypes.c_uint32,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint32),"
         "ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_void_p)]\n"
         "security.SecKeychainFindGenericPassword.restype=ctypes.c_int32\n"
         "security.SecKeychainItemFreeContent.argtypes=["
         "ctypes.c_void_p,ctypes.c_void_p]\n"
         "core.CFRelease.argtypes=[ctypes.c_void_p]\n"
         "interaction=security.SecKeychainSetUserInteractionAllowed(False)\n"
         "if interaction != 0: sys.exit(8)\n"
         "service=sys.argv[1].encode(); account=sys.argv[2].encode()\n"
         "length=ctypes.c_uint32(); data=ctypes.c_void_p(); "
         "item=ctypes.c_void_p()\n"
         "status=security.SecKeychainFindGenericPassword("
         "None,len(service),service,len(account),account,"
         "ctypes.byref(length),ctypes.byref(data),ctypes.byref(item))\n"
         "if data.value: security.SecKeychainItemFreeContent(None,data)\n"
         "if item.value: core.CFRelease(item)\n"
         "sys.exit(0 if status == int(sys.argv[3]) else 9)\n";
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/python3"];
    task.arguments = @[
        @"-c",
        program,
        service,
        account,
        [NSString stringWithFormat:@"%d", expectedStatus],
    ];
    task.standardInput = NSFileHandle.fileHandleWithNullDevice;
    task.standardOutput = [NSPipe pipe];
    task.standardError = [NSPipe pipe];
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    task.terminationHandler = ^(NSTask *completedTask) {
        (void)completedTask;
        dispatch_semaphore_signal(completed);
    };
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        return NO;
    }
    if (dispatch_semaphore_wait(
            completed,
            dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)
        ) != 0) {
        pid_t child = task.processIdentifier;
        kill(child, SIGTERM);
        if (dispatch_semaphore_wait(
                completed,
                dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)
            ) != 0) {
            kill(child, SIGKILL);
        }
    }
    [task waitUntilExit];
    task.terminationHandler = nil;
    return task.terminationReason == NSTaskTerminationReasonExit
        && task.terminationStatus == 0;
}

static BOOL PythonIsDenied(NSString *service, NSString *account) {
    // Interaction-disabled lookup must reach the ACL and reject the foreign
    // executable exactly; an interaction error does not attest that contract.
    return PythonReturnsStatus(service, account, errSecAuthFailed);
}

static int WriteJSON(NSDictionary *value) {
    NSError *error = nil;
    NSData *encoded = [NSJSONSerialization
        dataWithJSONObject:value
        options:NSJSONWritingSortedKeys
        error:&error];
    if (error != nil || encoded == nil) {
        return Fail(@"credential response serialization failed");
    }
    NSFileHandle *output = NSFileHandle.fileHandleWithStandardOutput;
    [output writeData:encoded];
    [output writeData:[NSData dataWithBytes:"\n" length:1]];
    return 0;
}

static int InitializeCredentials(NSString *account, NSString *executablePath) {
    for (NSDictionary *binding in Bindings().allValues) {
        NSString *value = RandomSecret();
        if (value == nil
            || AddSecret(
                binding[@"service"],
                account,
                value,
                executablePath
            ) != errSecSuccess) {
            return Fail(@"Keychain credential initialization failed");
        }
    }
    return 0;
}

static int CredentialStatus(
    NSString *account,
    NSString *executablePath
) {
    NSMutableDictionary *present = [NSMutableDictionary dictionary];
    BOOL complete = YES;
    for (NSString *environment in Bindings()) {
        NSData *secret = nil;
        OSStatus status = CopyProtectedSecret(
            Bindings()[environment][@"service"],
            account,
            executablePath,
            &secret
        );
        if (status != errSecSuccess && status != errSecItemNotFound) {
            return Fail(@"Keychain credential status failed");
        }
        BOOL found = status == errSecSuccess && DecodeSecret(secret) != nil;
        present[environment] = @(found);
        complete = complete && found;
    }
    int result = WriteJSON(@{
        @"present": present,
        @"schema_version": @1,
    });
    return result == 0 && complete ? 0 : 1;
}

static int RetireCredentials(
    NSString *account,
    NSString *executablePath
) {
    BOOL complete = YES;
    for (NSDictionary *binding in Bindings().allValues) {
        OSStatus status = RetireSecret(
            binding[@"service"],
            account,
            executablePath
        );
        if (status != errSecSuccess) {
            complete = NO;
        }
    }
    return complete
        ? 0
        : Fail(@"Keychain credential retirement failed");
}

static int RetirementStatus(NSString *account) {
    BOOL retired = YES;
    for (NSDictionary *binding in Bindings().allValues) {
        SecKeychainItemRef item = NULL;
        OSStatus status = CopyItem(
            binding[@"service"],
            account,
            &item
        );
        if (status == errSecSuccess) {
            retired = NO;
            CFRelease(item);
        } else if (status != errSecItemNotFound) {
            return Fail(@"Keychain retirement status failed");
        }
    }
    int result = WriteJSON(@{
        @"retired": @(retired),
        @"schema_version": @1,
    });
    return result == 0 && retired ? 0 : 1;
}

static int ResolveCredentials(
    NSString *account,
    NSString *executablePath
) {
    NSData *raw = ReadRequest();
    NSString *message = nil;
    NSArray *credentials = ParseRequest(raw, &message);
    if (credentials == nil) {
        return Fail(message);
    }
    NSMutableDictionary *values = [NSMutableDictionary dictionary];
    for (NSDictionary *item in credentials) {
        NSString *environment = item[@"environment"];
        NSData *secret = nil;
        OSStatus status = CopyProtectedSecret(
            Bindings()[environment][@"service"],
            account,
            executablePath,
            &secret
        );
        NSString *value = status == errSecSuccess
            ? DecodeSecret(secret)
            : nil;
        if (value == nil) {
            return Fail(@"required Keychain credential is unavailable");
        }
        values[environment] = value;
    }
    return WriteJSON(@{
        @"schema_version": @1,
        @"values": values,
    });
}

static int SelfTestACL(NSString *account, NSString *executablePath) {
    NSString *service = [@"io.nisavid.hindsight.self-test."
        stringByAppendingString:NSUUID.UUID.UUIDString.lowercaseString];
    NSString *foreignService = [@"io.nisavid.hindsight.self-test."
        stringByAppendingString:NSUUID.UUID.UUIDString.lowercaseString];
    NSString *anyService = [@"io.nisavid.hindsight.self-test."
        stringByAppendingString:NSUUID.UUID.UUIDString.lowercaseString];
    NSString *secret = RandomSecret();
    BOOL nativeRead = NO;
    BOOL pythonDenied = NO;
    BOOL retirementWorks = NO;
    BOOL foreignACLRejected = NO;
    BOOL separateAnyACLRejected = NO;
    BOOL cleanupComplete = NO;
    NSString *failureMessage = nil;
    @try {
        OSStatus setupStatus = secret == nil
            ? errSecInternalError
            : AddSecret(service, account, secret, executablePath);
        if (setupStatus != errSecSuccess) {
            fprintf(stderr, "Keychain ACL setup status: %d\n", setupStatus);
            failureMessage = @"Keychain ACL self-test setup failed";
        }
        NSData *observed = nil;
        if (failureMessage == nil) {
            nativeRead = CopyProtectedSecret(
                service,
                account,
                executablePath,
                &observed
            ) == errSecSuccess
                && [DecodeSecret(observed) isEqualToString:secret];
            pythonDenied = PythonIsDenied(service, account);
            retirementWorks = RetireSecret(
                service,
                account,
                executablePath
            ) == errSecSuccess;
            observed = nil;
            retirementWorks = retirementWorks
                && CopyProtectedSecret(
                    service,
                    account,
                    executablePath,
                    &observed
                ) == errSecItemNotFound;
            OSStatus foreignStatus = CreateSecret(
                foreignService,
                account,
                secret,
                @[executablePath, @"/usr/bin/python3"]
            );
            if (foreignStatus != errSecSuccess) {
                fprintf(
                    stderr,
                    "Keychain foreign ACL setup status: %d\n",
                    foreignStatus
                );
                failureMessage = @"Keychain ACL self-test setup failed";
            }
        }
        if (failureMessage == nil) {
            observed = nil;
            foreignACLRejected = CopyProtectedSecret(
                foreignService,
                account,
                executablePath,
                &observed
            ) == errSecAuthFailed;
            OSStatus anyStatus = CreateSecretWithSeparateAnyACL(
                anyService,
                account,
                secret,
                executablePath
            );
            if (anyStatus != errSecSuccess) {
                fprintf(
                    stderr,
                    "Keychain Any ACL setup status: %d\n",
                    anyStatus
                );
                failureMessage = @"Keychain ACL self-test setup failed";
            }
        }
        if (failureMessage == nil) {
            observed = nil;
            separateAnyACLRejected = CopyProtectedSecret(
                anyService,
                account,
                executablePath,
                &observed
            ) == errSecAuthFailed;
        }
    } @finally {
        BOOL deletedPrimary = DeleteSecret(service, account);
        BOOL deletedForeign = DeleteSecret(foreignService, account);
        BOOL deletedAny = DeleteSecret(anyService, account);
        cleanupComplete = deletedPrimary && deletedForeign && deletedAny;
    }
    if (!cleanupComplete) {
        return Fail(@"Keychain ACL self-test cleanup failed");
    }
    if (failureMessage != nil) {
        return Fail(failureMessage);
    }
    if (!nativeRead
        || !pythonDenied
        || !retirementWorks
        || !foreignACLRejected
        || !separateAnyACLRejected) {
        fprintf(
            stderr,
            "Keychain ACL results: native=%d python=%d retire=%d "
            "foreign=%d any=%d\n",
            nativeRead,
            pythonDenied,
            retirementWorks,
            foreignACLRejected,
            separateAnyACLRejected
        );
        return Fail(@"Keychain ACL self-test failed");
    }
    return WriteJSON(@{
        @"foreign_acl_rejected": @YES,
        @"native_read": @YES,
        @"python_denied": @YES,
        @"retirement_works": @YES,
        @"schema_version": @1,
        @"separate_any_acl_rejected": @YES,
    });
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *account = AccountName();
        NSString *executablePath = NSBundle.mainBundle.executablePath
            .stringByResolvingSymlinksInPath;
        if (account == nil || executablePath.length == 0) {
            return Fail(@"resolver identity is unavailable");
        }
        if (argc == 1) {
            return ResolveCredentials(account, executablePath);
        }
        if (argc == 2 && strcmp(argv[1], "--initialize") == 0) {
            return InitializeCredentials(account, executablePath);
        }
        if (argc == 2 && strcmp(argv[1], "--status") == 0) {
            return CredentialStatus(account, executablePath);
        }
        if (argc == 2 && strcmp(argv[1], "--retire") == 0) {
            return RetireCredentials(account, executablePath);
        }
        if (argc == 2 && strcmp(argv[1], "--retired-status") == 0) {
            return RetirementStatus(account);
        }
        if (argc == 2 && strcmp(argv[1], "--self-test-acl") == 0) {
            return SelfTestACL(account, executablePath);
        }
        return Fail(@"invalid credential resolver arguments");
    }
}
