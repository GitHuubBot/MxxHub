#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "MXXHUB_HOST_BREADCRUMB_SELFTEST_BYPASS_V20"

if len(sys.argv) != 2:
    raise SystemExit("usage: mxxhub_patch_host_v20.py <project-root>")

root = Path(sys.argv[1]).resolve()
vc_p = root / "MxxWineRuntimeViewController.m"
if not vc_p.is_file():
    raise SystemExit(f"ERROR: missing {vc_p}")

s = vc_p.read_text(encoding="utf-8")

if MARKER not in s:
    old_decl = '- (void)appendRuntimeLogLevel:(WGLogLevel)level tag:(NSString *)tag message:(NSString *)message;\n@end\n'
    new_decl = '''- (void)appendRuntimeLogLevel:(WGLogLevel)level tag:(NSString *)tag message:(NSString *)message;
- (void)writeHostBreadcrumb:(NSString *)message;
- (void)handleAppWillResignActive;
- (void)handleAppDidEnterBackground;
- (void)handleAppDidBecomeActive;
- (void)handleMemoryWarning;
@end
'''
    if old_decl not in s: raise SystemExit("ERROR: decl anchor")
    s = s.replace(old_decl, new_decl, 1)

    old_view = '''    UIApplication.sharedApplication.idleTimerDisabled = YES;
    [self setStatus:@"Preparing Hollow Knight x64 boot chain…"];
    [self startEngine];
    [self scheduleBootWatchdog];
}
'''
    new_view = '''    UIApplication.sharedApplication.idleTimerDisabled = YES;

    /* MXXHUB_HOST_BREADCRUMB_SELFTEST_BYPASS_V20 */
    NSNotificationCenter *nc = NSNotificationCenter.defaultCenter;
    [nc addObserver:self selector:@selector(handleAppWillResignActive)
               name:UIApplicationWillResignActiveNotification object:nil];
    [nc addObserver:self selector:@selector(handleAppDidEnterBackground)
               name:UIApplicationDidEnterBackgroundNotification object:nil];
    [nc addObserver:self selector:@selector(handleAppDidBecomeActive)
               name:UIApplicationDidBecomeActiveNotification object:nil];
    [nc addObserver:self selector:@selector(handleMemoryWarning)
               name:UIApplicationDidReceiveMemoryWarningNotification object:nil];

    [self writeHostBreadcrumb:@"V20 viewDidLoad — runtime screen created"];
    [self setStatus:@"Preparing Hollow Knight x64 boot chain…"];
    [self startEngine];
    [self scheduleBootWatchdog];
}
'''
    if old_view not in s: raise SystemExit("ERROR: viewDidLoad anchor")
    s = s.replace(old_view, new_view, 1)

    s = s.replace(
        '''- (void)dealloc {
    [self shutdownEngine];
}
''',
        '''- (void)dealloc {
    [self writeHostBreadcrumb:@"V20 dealloc entered"];
    [NSNotificationCenter.defaultCenter removeObserver:self];
    [self shutdownEngine];
}
''', 1)

    old_disappear = '''- (void)viewDidDisappear:(BOOL)animated {
    [super viewDidDisappear:animated];
    if (self.presentingViewController == nil || self.navigationController == nil) {
        [self shutdownEngine];
    }
}
'''
    new_disappear = '''- (void)viewDidDisappear:(BOOL)animated {
    [super viewDidDisappear:animated];
    [self writeHostBreadcrumb:@"V20 viewDidDisappear"];

    BOOL actuallyClosing =
        self.isBeingDismissed ||
        self.navigationController.isBeingDismissed ||
        self.isMovingFromParentViewController;

    if (actuallyClosing) {
        [self writeHostBreadcrumb:@"V20 viewDidDisappear — actual close, shutting down engine"];
        [self shutdownEngine];
    } else {
        [self writeHostBreadcrumb:@"V20 viewDidDisappear — temporary/system transition, engine kept alive"];
    }
}
'''
    if old_disappear not in s: raise SystemExit("ERROR: disappear anchor")
    s = s.replace(old_disappear, new_disappear, 1)

    old_selftest = '''    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        bool selfTestOK = wg_selftest_run();
        dispatch_async(dispatch_get_main_queue(), ^{
            [self setStatus:(selfTestOK ? @"Hollow Knight boot: CPU self-test passed — creating Blink…" : @"CPU self-test incomplete — Hollow Knight boot will stop if Blink is unavailable")];
        });

        self->_engine = wg_engine_create();
'''
    new_selftest = '''    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        [self writeHostBreadcrumb:@"V20 engine worker entered — self-test bypassed"];

        bool selfTestOK = true;
        (void)selfTestOK;
        [self appendRuntimeLogLevel:WG_LOG_INFO
                                tag:@"MxxHub"
                            message:@"SELFTEST V20 BYPASSED: launching game VM directly"];
        dispatch_async(dispatch_get_main_queue(), ^{
            [self setStatus:@"Hollow Knight boot: direct Blink launch — self-test bypassed"];
        });

        [self writeHostBreadcrumb:@"V20 before wg_engine_create"];
        self->_engine = wg_engine_create();
'''
    if old_selftest not in s: raise SystemExit("ERROR: selftest anchor")
    s = s.replace(old_selftest, new_selftest, 1)

    transitions = [
        ('        if (!self->_engine || !wg_engine_init(self->_engine)) {\n',
         '        [self writeHostBreadcrumb:@"V20 after wg_engine_create — entering wg_engine_init"];\n        if (!self->_engine || !wg_engine_init(self->_engine)) {\n            [self writeHostBreadcrumb:@"V20 wg_engine_init FAILED"];\n'),
        ('        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:@"Hollow Knight boot: mapping EXE + UnityPlayer…"]; });\n        bool loaded = wg_engine_load_pe(self->_engine, self.executablePath.UTF8String);\n',
         '        [self writeHostBreadcrumb:@"V20 wg_engine_init OK — before wg_engine_load_pe"];\n        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:@"Hollow Knight boot: mapping EXE + UnityPlayer…"]; });\n        bool loaded = wg_engine_load_pe(self->_engine, self.executablePath.UTF8String);\n'),
        ('        bool started = wg_engine_run(self->_engine);\n',
         '        [self writeHostBreadcrumb:@"V20 PE load OK — before wg_engine_run"];\n        bool started = wg_engine_run(self->_engine);\n'),
        ('        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:@"Hollow Knight boot chain running — waiting for Unity window…"]; });\n        [self startEngineThread];\n',
         '        [self writeHostBreadcrumb:@"V20 wg_engine_run OK — starting engine thread"];\n        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:@"Hollow Knight boot chain running — waiting for Unity window…"]; });\n        [self startEngineThread];\n'),
    ]
    for old,new in transitions:
        if old not in s: raise SystemExit("ERROR: transition anchor")
        s = s.replace(old,new,1)

    old_thread = '    [self.engineThread start];\n}\n'
    new_thread = '    [self.engineThread start];\n    [self writeHostBreadcrumb:@"V20 engine NSThread started"];\n}\n'
    if old_thread not in s: raise SystemExit("ERROR: thread anchor")
    s = s.replace(old_thread,new_thread,1)

    old_tail = '''    if (looksImportant) {
        self.lastDiagnosticLine = line;
    }

    self.logTextView.text = [self.runtimeLogLines componentsJoinedByString:@"\\n"];
'''
    new_tail = '''    if (looksImportant) {
        self.lastDiagnosticLine = line;
    }

    static NSUInteger v20PersistCounter = 0;
    v20PersistCounter++;
    if ((v20PersistCounter % 50) == 0 || level >= WG_LOG_ERROR) {
        [self saveRuntimeLog];
    }

    self.logTextView.text = [self.runtimeLogLines componentsJoinedByString:@"\\n"];
'''
    if old_tail not in s: raise SystemExit("ERROR: append anchor")
    s = s.replace(old_tail,new_tail,1)

    methods = r'''- (void)writeHostBreadcrumb:(NSString *)message {
    NSArray<NSURL *> *docs = [NSFileManager.defaultManager
        URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask];
    NSURL *docsURL = docs.firstObject;
    if (!docsURL) return;

    NSURL *dir = [docsURL URLByAppendingPathComponent:@"MxxHubLogs" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:dir
                           withIntermediateDirectories:YES
                                            attributes:nil error:nil];

    NSURL *fileURL = [dir URLByAppendingPathComponent:@"host-latest.txt"];
    NSString *stamp = [NSDate.date descriptionWithLocale:nil];
    NSString *line = [NSString stringWithFormat:@"%@ | %@\n", stamp, message ?: @"<nil>"];

    NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:fileURL.path];
    if (!fh) {
        [line writeToURL:fileURL atomically:YES encoding:NSUTF8StringEncoding error:nil];
        return;
    }
    @try {
        [fh seekToEndOfFile];
        [fh writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
        [fh synchronizeFile];
    } @catch (__unused NSException *ex) {
    } @finally {
        [fh closeFile];
    }
}

- (void)handleAppWillResignActive {
    [self writeHostBreadcrumb:@"V20 UIApplicationWillResignActive"];
    [self saveRuntimeLog];
}

- (void)handleAppDidEnterBackground {
    [self writeHostBreadcrumb:@"V20 UIApplicationDidEnterBackground"];
    [self saveRuntimeLog];
}

- (void)handleAppDidBecomeActive {
    [self writeHostBreadcrumb:@"V20 UIApplicationDidBecomeActive"];
}

- (void)handleMemoryWarning {
    [self writeHostBreadcrumb:@"V20 MEMORY WARNING received"];
    [self appendRuntimeLogLevel:WG_LOG_WARN tag:@"MxxHub"
                        message:@"HOST V20: UIApplication memory warning received"];
    [self saveRuntimeLog];
}

'''
    anchor='- (void)showDiagnostics {\n'
    if anchor not in s: raise SystemExit("ERROR: diagnostics anchor")
    s=s.replace(anchor,methods+anchor,1)

    s=s.replace(
        '- (void)shutdownEngine {\n    _engineThreadRunning = NO;\n',
        '- (void)shutdownEngine {\n    [self writeHostBreadcrumb:@"V20 shutdownEngine entered"];\n    _engineThreadRunning = NO;\n',1)

    old_destroy='        wg_engine_destroy(_engine);\n        _engine = NULL;\n'
    new_destroy='        [self writeHostBreadcrumb:@"V20 before wg_engine_destroy"];\n        wg_engine_destroy(_engine);\n        _engine = NULL;\n        [self writeHostBreadcrumb:@"V20 after wg_engine_destroy"];\n'
    if old_destroy not in s: raise SystemExit("ERROR: destroy anchor")
    s=s.replace(old_destroy,new_destroy,1)

    vc_p.write_text(s,encoding='utf-8')

final=vc_p.read_text(encoding='utf-8')
for token in [MARKER,"SELFTEST V20 BYPASSED","host-latest.txt","V20 before wg_engine_create","V20 wg_engine_run OK","V20 MEMORY WARNING received"]:
    if token not in final: raise SystemExit("ERROR: verify "+token)
if "bool selfTestOK = wg_selftest_run();" in final:
    raise SystemExit("ERROR: selftest still active")
print("MXXHUB_HOST_BREADCRUMB_SELFTEST_BYPASS_V20_OK")
