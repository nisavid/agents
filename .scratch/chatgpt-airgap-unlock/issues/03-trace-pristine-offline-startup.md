Type: research
Status: closed
Assignee: nisavid

## Question

Using a disposable app copy and isolated never-authenticated profile, what processes, files, keychain items, IPC calls, network attempts, timeouts, and state transitions occur from launch through the login wall, and which missing inputs distinguish an intentional no-auth mode from a merely hidden modal?

## Resolution

[Exact-build startup trace](https://github.com/nisavid/agents/blob/716456a6af776b72a6643f85a0019f79bfb0564a/.scratch/chatgpt-airgap-unlock/research/03-pristine-offline-startup.md) reproduces the pristine state under explicit network, home-directory, and keychain denial: the bundled host returns `account: null` with `requiresOpenaiAuth: true`, and the renderer deterministically reaches the login wall. Hiding or navigating around that UI is insufficient. The semantic no-auth input is the same account response with `requiresOpenaiAuth: false`, which the configured custom-provider path can supply naturally after a cold start.
