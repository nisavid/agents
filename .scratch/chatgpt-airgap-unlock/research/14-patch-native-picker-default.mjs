#!/usr/bin/env node

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import {
  link,
  lstat,
  mkdtemp,
  open,
  readFile,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SOURCE = Buffer.from(
  'let r={properties:n,title:`Select Project Root`},i=c.BrowserWindow.fromWebContents(e),a=i==null?await c.dialog.showOpenDialog(r):await c.dialog.showOpenDialog(i,r);',
);
const PATCHED = Buffer.from(
  'let r={properties:n,title:`Select Project Root`,defaultPath:process.env.NDP},i=c.BrowserWindow.fromWebContents(e),a=await c.dialog.showOpenDialog(...(i?[i,r]:[r]));',
);
const MAIN_PATH = [".vite", "build", "main-DcVqMbYE.js"];

assert.equal(SOURCE.length, 164);
assert.equal(PATCHED.length, SOURCE.length);

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function align4(value) {
  return value + ((4 - (value % 4)) % 4);
}

function findOffsets(haystack, needle) {
  const offsets = [];
  let offset = haystack.indexOf(needle);
  while (offset !== -1) {
    offsets.push(offset);
    offset = haystack.indexOf(needle, offset + 1);
  }
  return offsets;
}

function getMainEntry(header) {
  let entry = header;
  for (const part of MAIN_PATH) {
    assert.ok(entry?.files?.[part], `missing ASAR entry: ${MAIN_PATH.join("/")}`);
    entry = entry.files[part];
  }
  assert.equal(typeof entry.size, "number");
  assert.match(entry.offset, /^\d+$/);
  assert.equal(entry.integrity?.algorithm, "SHA256");
  assert.equal(entry.integrity?.blockSize, 4 * 1024 * 1024);
  assert.equal(entry.integrity?.blocks?.length, 1);
  assert.equal(entry.integrity.blocks[0], entry.integrity.hash);
  return entry;
}

function integrityFor(value, blockSize) {
  const blocks = [];
  for (let offset = 0; offset < value.length; offset += blockSize) {
    blocks.push(sha256(value.subarray(offset, Math.min(offset + blockSize, value.length))));
  }
  if (value.length === 0) blocks.push(sha256(value));
  return { blocks, hash: sha256(value) };
}

async function readExactly(handle, length, position, label) {
  const value = Buffer.alloc(length);
  let transferred = 0;
  while (transferred < length) {
    const { bytesRead } = await handle.read(
      value,
      transferred,
      length - transferred,
      position + transferred,
    );
    assert.ok(bytesRead > 0, `short read for ${label}`);
    transferred += bytesRead;
  }
  return value;
}

async function writeExactly(handle, value, position, label) {
  let transferred = 0;
  while (transferred < value.length) {
    const { bytesWritten } = await handle.write(
      value,
      transferred,
      value.length - transferred,
      position + transferred,
    );
    assert.ok(bytesWritten > 0, `short write for ${label}`);
    transferred += bytesWritten;
  }
}

async function inspectAsar(handle, fileSize) {
  assert.ok(fileSize >= 16, "target is too small to contain an ASAR header");
  const sizePickle = await readExactly(handle, 8, 0, "ASAR size pickle");
  assert.equal(sizePickle.readUInt32LE(0), 4, "unexpected ASAR size pickle");
  const headerSize = sizePickle.readUInt32LE(4);
  assert.ok(headerSize >= 8, "invalid ASAR header size");
  assert.ok(8 + headerSize <= fileSize, "ASAR header exceeds target size");

  const headerPickle = await readExactly(handle, headerSize, 8, "ASAR header pickle");
  const payloadSize = headerPickle.readUInt32LE(0);
  const headerStringSize = headerPickle.readUInt32LE(4);
  assert.equal(payloadSize, align4(4 + headerStringSize));
  assert.equal(headerSize, 4 + payloadSize);
  const headerString = Buffer.from(headerPickle.subarray(8, 8 + headerStringSize));
  const header = JSON.parse(headerString.toString("utf8"));
  const entry = getMainEntry(header);
  const mainFileOffset = 8 + headerSize + Number(entry.offset);
  assert.ok(mainFileOffset + entry.size <= fileSize, "main entry exceeds target size");
  const mainFile = await readExactly(handle, entry.size, mainFileOffset, "main entry");
  const computedIntegrity = integrityFor(mainFile, entry.integrity.blockSize);
  assert.deepEqual(entry.integrity.blocks, computedIntegrity.blocks);
  assert.equal(entry.integrity.hash, computedIntegrity.hash);

  return {
    entry,
    headerSha256: sha256(headerString),
    headerSize,
    headerString,
    mainFile,
    mainFileOffset,
    patchedOffsets: findOffsets(mainFile, PATCHED),
    sourceOffsets: findOffsets(mainFile, SOURCE),
  };
}

function requirePayloadState(state, expectedSourceCount, expectedPatchedCount) {
  assert.equal(
    state.sourceOffsets.length,
    expectedSourceCount,
    `expected ${expectedSourceCount} source payload(s), found ${state.sourceOffsets.length}`,
  );
  assert.equal(
    state.patchedOffsets.length,
    expectedPatchedCount,
    `expected ${expectedPatchedCount} patched payload(s), found ${state.patchedOffsets.length}`,
  );
}

async function requireDescriptorMatchesTarget(handle, targetPath) {
  const descriptorStat = await handle.stat();
  assert.equal(descriptorStat.isFile(), true, "opened target must be a regular file");
  assert.equal(descriptorStat.nlink, 1, "opened target must have exactly one link");
  const targetStat = await lstat(targetPath);
  assert.equal(targetStat.isSymbolicLink(), false, "target path must not be a symlink");
  assert.equal(targetStat.isFile(), true, "target path must be a regular file");
  assert.equal(targetStat.nlink, 1, "target path must have exactly one link");
  assert.equal(targetStat.dev, descriptorStat.dev, "target path does not match opened descriptor device");
  assert.equal(targetStat.ino, descriptorStat.ino, "target path does not match opened descriptor inode");
  assert.equal(targetStat.size, descriptorStat.size, "target path does not match opened descriptor size");
  return descriptorStat.size;
}

async function inspectTarget(targetPath, writable) {
  const canonicalPath = await realpath(targetPath);
  assert.equal(canonicalPath, targetPath, "target path must already be canonical");
  const flags = (writable ? constants.O_RDWR : constants.O_RDONLY) | constants.O_NOFOLLOW;
  const handle = await open(targetPath, flags);
  try {
    const fileSize = await requireDescriptorMatchesTarget(handle, targetPath);
    return { canonicalPath, fileSize, handle };
  } catch (error) {
    await handle.close();
    throw error;
  }
}

async function patchTarget(mode, targetPath) {
  const writable = mode === "apply";
  const { canonicalPath, fileSize, handle } = await inspectTarget(targetPath, writable);
  try {
    const before = await inspectAsar(handle, fileSize);
    if (mode === "verify-source") {
      requirePayloadState(before, 1, 0);
    } else if (mode === "verify-patched") {
      requirePayloadState(before, 0, 1);
    } else {
      assert.equal(mode, "apply", `unsupported mode: ${mode}`);
      requirePayloadState(before, 1, 0);
      const patchedMainFile = Buffer.from(before.mainFile);
      PATCHED.copy(patchedMainFile, before.sourceOffsets[0]);
      const patchedIntegrity = integrityFor(
        patchedMainFile,
        before.entry.integrity.blockSize,
      );
      assert.equal(patchedIntegrity.blocks.length, 1);
      const sourceHash = Buffer.from(before.entry.integrity.hash);
      const patchedHash = Buffer.from(patchedIntegrity.hash);
      assert.equal(sourceHash.length, patchedHash.length);
      const integrityOffsets = findOffsets(before.headerString, sourceHash);
      assert.equal(
        integrityOffsets.length,
        2,
        "expected main-file hash exactly twice in ASAR header",
      );
      const patchOffset = before.mainFileOffset + before.sourceOffsets[0];
      await requireDescriptorMatchesTarget(handle, targetPath);
      await writeExactly(handle, PATCHED, patchOffset, "main payload");
      for (const headerOffset of integrityOffsets) {
        await writeExactly(
          handle,
          patchedHash,
          16 + headerOffset,
          "ASAR header hash",
        );
      }
      await handle.sync();
      await requireDescriptorMatchesTarget(handle, targetPath);
      const after = await inspectAsar(handle, fileSize);
      requirePayloadState(after, 0, 1);
      assert.equal(after.mainFileOffset + after.patchedOffsets[0], patchOffset);
      assert.equal(after.entry.integrity.hash, patchedIntegrity.hash);
      assert.notEqual(after.headerSha256, before.headerSha256);
    }

    const finalState = await inspectAsar(handle, fileSize);
    await requireDescriptorMatchesTarget(handle, targetPath);
    return {
      asarHeaderSha256: finalState.headerSha256,
      asarHeaderSize: finalState.headerSize,
      fileSize,
      mainFileSha256: finalState.entry.integrity.hash,
      mode,
      patchOffset:
        finalState.patchedOffsets.length === 1
          ? finalState.mainFileOffset + finalState.patchedOffsets[0]
          : null,
      patchedPayloadSha256: sha256(PATCHED),
      sourcePayloadSha256: sha256(SOURCE),
      targetPath: canonicalPath,
    };
  } finally {
    await handle.close();
  }
}

function createAsarFixture(payload, extraHashCopies = 0) {
  const payloadIntegrity = integrityFor(payload, 4 * 1024 * 1024);
  const headerString = Buffer.from(
    JSON.stringify({
      debugHashes: Array(extraHashCopies).fill(payloadIntegrity.hash),
      files: {
        ".vite": {
          files: {
            build: {
              files: {
                "main-DcVqMbYE.js": {
                  size: payload.length,
                  offset: "0",
                  integrity: {
                    algorithm: "SHA256",
                    hash: payloadIntegrity.hash,
                    blockSize: 4 * 1024 * 1024,
                    blocks: payloadIntegrity.blocks,
                  },
                },
              },
            },
          },
        },
      },
    }),
  );
  const headerPayloadSize = align4(4 + headerString.length);
  const headerPickle = Buffer.alloc(4 + headerPayloadSize);
  headerPickle.writeUInt32LE(headerPayloadSize, 0);
  headerPickle.writeUInt32LE(headerString.length, 4);
  headerString.copy(headerPickle, 8);
  const sizePickle = Buffer.alloc(8);
  sizePickle.writeUInt32LE(4, 0);
  sizePickle.writeUInt32LE(headerPickle.length, 4);
  return {
    bytes: Buffer.concat([sizePickle, headerPickle, payload]),
    payloadOffset: sizePickle.length + headerPickle.length,
  };
}

async function expectFailure(action, expectedMessage) {
  try {
    await action();
  } catch (error) {
    assert.match(String(error), expectedMessage);
    return;
  }
  assert.fail(`expected failure matching ${expectedMessage}`);
}

async function selfTest() {
  const root = await realpath(
    await mkdtemp(join(tmpdir(), "chatgpt-native-picker-patch.")),
  );
  try {
    const target = join(root, "app.asar");
    const sourcePayload = Buffer.concat([
      Buffer.from("prefix"),
      SOURCE,
      Buffer.from("suffix"),
    ]);
    const original = createAsarFixture(sourcePayload);
    await writeFile(target, original.bytes);
    const sourceInspection = await patchTarget("verify-source", target);
    const applied = await patchTarget("apply", target);
    assert.equal(applied.fileSize, original.bytes.length);
    assert.equal(
      applied.patchOffset,
      original.payloadOffset + Buffer.byteLength("prefix"),
    );
    assert.notEqual(applied.asarHeaderSha256, sourceInspection.asarHeaderSha256);
    const patchedInspection = await patchTarget("verify-patched", target);
    assert.equal(patchedInspection.asarHeaderSha256, applied.asarHeaderSha256);
    const patchedPayload = Buffer.concat([
      Buffer.from("prefix"),
      PATCHED,
      Buffer.from("suffix"),
    ]);
    assert.equal(patchedInspection.mainFileSha256, sha256(patchedPayload));
    assert.deepEqual(await readFile(target), createAsarFixture(patchedPayload).bytes);

    await expectFailure(
      () => patchTarget("apply", target),
      /expected 1 source payload\(s\), found 0/,
    );

    const wrongHeaderCount = join(root, "wrong-header-count.asar");
    const wrongHeaderFixture = createAsarFixture(sourcePayload, 1);
    await writeFile(wrongHeaderCount, wrongHeaderFixture.bytes);
    await expectFailure(
      () => patchTarget("apply", wrongHeaderCount),
      /expected main-file hash exactly twice in ASAR header/,
    );
    assert.deepEqual(await readFile(wrongHeaderCount), wrongHeaderFixture.bytes);

    const duplicate = join(root, "duplicate.asar");
    await writeFile(duplicate, createAsarFixture(Buffer.concat([SOURCE, SOURCE])).bytes);
    await expectFailure(
      () => patchTarget("apply", duplicate),
      /expected 1 source payload\(s\), found 2/,
    );

    const missing = join(root, "missing.asar");
    await writeFile(missing, createAsarFixture(Buffer.from("no matching payload")).bytes);
    await expectFailure(
      () => patchTarget("apply", missing),
      /expected 1 source payload\(s\), found 0/,
    );

    const linked = join(root, "linked.asar");
    await symlink(target, linked);
    await expectFailure(
      () => patchTarget("verify-patched", linked),
      /ELOOP|symlink|already be canonical/,
    );

    const hardLinked = join(root, "hard-linked.asar");
    await link(target, hardLinked);
    await expectFailure(
      () => patchTarget("verify-patched", target),
      /exactly one link/,
    );
    await rm(hardLinked);

    const raceTarget = join(root, "race-target.asar");
    const raceReplacement = join(root, "race-replacement.asar");
    await writeFile(raceTarget, original.bytes);
    await writeFile(raceReplacement, original.bytes);
    const raceHandle = await open(raceTarget, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      await rename(raceReplacement, raceTarget);
      await expectFailure(
        () => requireDescriptorMatchesTarget(raceHandle, raceTarget),
        /exactly one link|does not match opened descriptor inode/,
      );
    } finally {
      await raceHandle.close();
    }
  } finally {
    await rm(root, { force: true, recursive: true });
  }
  process.stdout.write("native picker ASAR patch self-test passed\n");
}

async function main() {
  const [mode, targetPath, ...extra] = process.argv.slice(2);
  if (mode === "--self-test" && targetPath === undefined && extra.length === 0) {
    await selfTest();
    return;
  }
  assert.equal(extra.length, 0, "usage: patcher <apply|verify-source|verify-patched> <app.asar>");
  assert.match(mode ?? "", /^(apply|verify-source|verify-patched)$/);
  assert.ok(targetPath, "target app.asar path is required");
  process.stdout.write(`${JSON.stringify(await patchTarget(mode, targetPath))}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error}\n`);
  process.exitCode = 1;
});
