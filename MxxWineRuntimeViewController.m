#import "MxxWineRuntimeViewController.h"
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <unistd.h>

#import "WGMetalView.h"
#import "WGCompositor.h"
#include "wg_engine.h"
#include "wg_log.h"
#include "wg_selftest.h"
#include "wg_win32_windows.h"

@interface MxxWineRuntimeViewController ()
@property (nonatomic, copy, readwrite) NSString *executablePath;
@property (nonatomic, strong) WGMetalView *metalView;
@property (nonatomic, strong) WGCompositor *compositor;
@property (nonatomic, strong) id<MTLCommandQueue> commandQueue;
@property (nonatomic, strong) CADisplayLink *displayLink;
@property (nonatomic, strong) UILabel *statusLabel;
@property (nonatomic, strong) UIVisualEffectView *statusPanel;
@property (nonatomic, strong) UIButton *closeButton;
@property (nonatomic, strong) UIButton *logButton;
@property (nonatomic, strong) UIVisualEffectView *diagnosticsPanel;
@property (nonatomic, strong) UITextView *logTextView;
@property (nonatomic, strong) UIButton *logCopyButton;
@property (nonatomic, strong) UIButton *hideLogButton;
@property (nonatomic, strong) NSThread *engineThread;
@property (nonatomic, strong) NSMutableArray<NSString *> *runtimeLogLines;
@property (nonatomic, copy) NSString *lastDiagnosticLine;
@property (nonatomic, copy) NSString *savedLogPath;
@property (nonatomic) NSInteger bootCheckpoint;
@property (nonatomic) BOOL sawGuestWindow;
@property (nonatomic) BOOL watchdogTriggered;
@property (nonatomic) BOOL bootFatalError;
@property (nonatomic) BOOL engineStartScheduled;
@property (nonatomic) BOOL engineStarted;
@property (nonatomic) BOOL runtimeClosing;
@property (nonatomic) NSInteger v38ReadyDrawableCount;
@property (nonatomic) CFTimeInterval v38ViewAppearedAt;
- (void)appendRuntimeLogLevel:(WGLogLevel)level tag:(NSString *)tag message:(NSString *)message;
- (void)writeHostBreadcrumb:(NSString *)message;
- (void)handleAppWillResignActive;
- (void)handleAppDidEnterBackground;
- (void)handleAppDidBecomeActive;
- (void)handleMemoryWarning;
- (void)saveCrashSnapshotWithReason:(NSString *)reason;
@end

static NSString *MxxLogLevelName(WGLogLevel level) {
    switch (level) {
        case WG_LOG_DEBUG: return @"DEBUG";
        case WG_LOG_INFO:  return @"INFO";
        case WG_LOG_WARN:  return @"WARN";
        case WG_LOG_ERROR: return @"ERROR";
        case WG_LOG_FATAL: return @"FATAL";
    }
    return @"LOG";
}

static void MxxRuntimeLogCallback(WGLogLevel level, const char *tag, const char *message, void *userdata) {
    if (!userdata) return;
    MxxWineRuntimeViewController *controller = (__bridge MxxWineRuntimeViewController *)userdata;
    NSString *tagString = tag ? [NSString stringWithUTF8String:tag] : @"runtime";
    NSString *messageString = message ? [NSString stringWithUTF8String:message] : @"";
    if (!tagString) tagString = @"runtime";
    if (!messageString) messageString = @"<invalid UTF-8 log message>";

    dispatch_async(dispatch_get_main_queue(), ^{
        [controller appendRuntimeLogLevel:level tag:tagString message:messageString];
    });
}

static NSString * const MxxAlwaysShowLogKey = @"MxxHubAlwaysShowLog";
static NSString * const MxxCopyLogOnCrashKey = @"MxxHubCopyLogOnCrash";

static BOOL MxxAlwaysShowLogEnabled(void) {
    return [NSUserDefaults.standardUserDefaults boolForKey:MxxAlwaysShowLogKey];
}

static BOOL MxxCopyLogOnCrashEnabled(void) {
    id value = [NSUserDefaults.standardUserDefaults objectForKey:MxxCopyLogOnCrashKey];
    return value == nil ? YES : [value boolValue];
}

@implementation MxxWineRuntimeViewController {
    WGEngine *_engine;
    volatile BOOL _engineThreadRunning;
}

/* MXXHUB_WINDOWS_V38_STABLE_IMMERSIVE_GUEST_MODE */
- (BOOL)prefersStatusBarHidden {
    return YES;
}

- (BOOL)prefersHomeIndicatorAutoHidden {
    return YES;
}

- (UIRectEdge)preferredScreenEdgesDeferringSystemGestures {
    return UIRectEdgeAll;
}

- (instancetype)initWithExecutablePath:(NSString *)executablePath {
    self = [super initWithNibName:nil bundle:nil];
    if (self) {
        _executablePath = [executablePath copy];
        _engine = NULL;
        _engineThreadRunning = NO;
        _runtimeLogLines = [NSMutableArray array];
        _bootCheckpoint = 0;
        _sawGuestWindow = NO;
        _watchdogTriggered = NO;
        _bootFatalError = NO;
        _engineStartScheduled = NO;
        _engineStarted = NO;
        _runtimeClosing = NO;
        _v38ReadyDrawableCount = 0;
        _v38ViewAppearedAt = 0;
        self.modalPresentationStyle = UIModalPresentationFullScreen;
    }
    return self;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
    [self setupMetal];
    [self setupOverlay];
    [self setupDiagnosticsPanel];
    [self setupTapHandler];
    [self startDisplayLink];
    UIApplication.sharedApplication.idleTimerDisabled = YES;

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

    [self writeHostBreadcrumb:@"V38 viewDidLoad — runtime screen created; engine start requires Metal drawable gate"];
    [self setStatus:@"Preparing Hollow Knight x64 boot chain…"];
}

- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (self.runtimeClosing || self.engineStarted || self.engineStartScheduled) return;

    /* MXXHUB_WINDOWS_V38_METAL_DRAWABLE_START_GATE
     * A fixed 200 ms delay was still nondeterministic on a cold app launch.
     * Do not start Blink/Unity until the fullscreen controller is attached to a
     * real UIWindow AND the CAMetalLayer has produced several real drawables.
     * renderFrame: owns the final transition into startEngine.
     */
    self.engineStartScheduled = YES;
    self.v38ReadyDrawableCount = 0;
    self.v38ViewAppearedAt = CACurrentMediaTime();
    [self writeHostBreadcrumb:@"V38 viewDidAppear — waiting for 3 Metal drawables before engine start"];
    [self setStatus:@"Preparing fullscreen Metal surface before starting Hollow Knight…"];
}

- (void)dealloc {
    [self writeHostBreadcrumb:@"V20 dealloc entered"];
    [NSNotificationCenter.defaultCenter removeObserver:self];
    [self shutdownEngine];
}

- (void)viewDidDisappear:(BOOL)animated {
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

- (void)setupMetal {
    self.metalView = [[WGMetalView alloc] initWithFrame:self.view.bounds];
    self.metalView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.metalView];

    /* MXXHUB_WINDOWS_V47_METAL_COLD_START_RECOVERY
     * On a cold presentation WGMetalView's CAMetalLayer can briefly exist
     * before its device/queue path is usable. V38's drawable gate then never
     * advances because renderFrame used to return before attempting recovery.
     */
    id<MTLDevice> device = self.metalView.metalLayer.device;
    if (!device) {
        device = MTLCreateSystemDefaultDevice();
        if (device) self.metalView.metalLayer.device = device;
    }
    if (device) {
        self.commandQueue = [device newCommandQueue];
        self.compositor = [[WGCompositor alloc] initWithDevice:device];
    }
}

- (void)setupOverlay {
    self.statusPanel = [[UIVisualEffectView alloc] initWithEffect:[UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemUltraThinMaterialDark]];
    UIVisualEffectView *panel = self.statusPanel;
    panel.translatesAutoresizingMaskIntoConstraints = NO;
    panel.layer.cornerRadius = 14.0;
    panel.clipsToBounds = YES;
    [self.view addSubview:panel];

    self.statusLabel = [[UILabel alloc] init];
    self.statusLabel.translatesAutoresizingMaskIntoConstraints = NO;
    self.statusLabel.textColor = UIColor.whiteColor;
    self.statusLabel.font = [UIFont monospacedSystemFontOfSize:12 weight:UIFontWeightMedium];
    self.statusLabel.numberOfLines = 6;
    self.statusLabel.lineBreakMode = NSLineBreakByTruncatingMiddle;
    [panel.contentView addSubview:self.statusLabel];

    self.logButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.logButton.translatesAutoresizingMaskIntoConstraints = NO;
    [self.logButton setTitle:@"Log" forState:UIControlStateNormal];
    self.logButton.titleLabel.font = [UIFont boldSystemFontOfSize:15];
    [self.logButton addTarget:self action:@selector(logTapped) forControlEvents:UIControlEventTouchUpInside];
    [panel.contentView addSubview:self.logButton];

    self.closeButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.closeButton.translatesAutoresizingMaskIntoConstraints = NO;
    [self.closeButton setTitle:@"Done" forState:UIControlStateNormal];
    self.closeButton.titleLabel.font = [UIFont boldSystemFontOfSize:16];
    [self.closeButton addTarget:self action:@selector(closeTapped) forControlEvents:UIControlEventTouchUpInside];
    [panel.contentView addSubview:self.closeButton];

    [NSLayoutConstraint activateConstraints:@[
        [panel.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:12],
        [panel.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-12],
        [panel.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:8],
        [self.statusLabel.leadingAnchor constraintEqualToAnchor:panel.contentView.leadingAnchor constant:12],
        [self.statusLabel.topAnchor constraintEqualToAnchor:panel.contentView.topAnchor constant:10],
        [self.statusLabel.bottomAnchor constraintEqualToAnchor:panel.contentView.bottomAnchor constant:-10],
        [self.logButton.leadingAnchor constraintGreaterThanOrEqualToAnchor:self.statusLabel.trailingAnchor constant:8],
        [self.logButton.centerYAnchor constraintEqualToAnchor:panel.contentView.centerYAnchor],
        [self.logButton.widthAnchor constraintEqualToConstant:48],
        [self.closeButton.leadingAnchor constraintEqualToAnchor:self.logButton.trailingAnchor constant:6],
        [self.closeButton.trailingAnchor constraintEqualToAnchor:panel.contentView.trailingAnchor constant:-12],
        [self.closeButton.centerYAnchor constraintEqualToAnchor:panel.contentView.centerYAnchor],
        [self.closeButton.widthAnchor constraintEqualToConstant:58]
    ]];
}

- (void)setupDiagnosticsPanel {
    self.diagnosticsPanel = [[UIVisualEffectView alloc] initWithEffect:[UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemMaterialDark]];
    self.diagnosticsPanel.translatesAutoresizingMaskIntoConstraints = NO;
    self.diagnosticsPanel.layer.cornerRadius = 16.0;
    self.diagnosticsPanel.clipsToBounds = YES;
    self.diagnosticsPanel.hidden = !MxxAlwaysShowLogEnabled();
    [self.view addSubview:self.diagnosticsPanel];

    UILabel *title = [[UILabel alloc] init];
    title.translatesAutoresizingMaskIntoConstraints = NO;
    title.text = @"MxxHub compatibility log";
    title.textColor = UIColor.whiteColor;
    title.font = [UIFont boldSystemFontOfSize:14];
    [self.diagnosticsPanel.contentView addSubview:title];

    self.logCopyButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.logCopyButton.translatesAutoresizingMaskIntoConstraints = NO;
    [self.logCopyButton setTitle:@"Copy Log" forState:UIControlStateNormal];
    self.logCopyButton.titleLabel.font = [UIFont boldSystemFontOfSize:13];
    [self.logCopyButton addTarget:self action:@selector(copyLogTapped) forControlEvents:UIControlEventTouchUpInside];
    [self.diagnosticsPanel.contentView addSubview:self.logCopyButton];

    self.hideLogButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.hideLogButton.translatesAutoresizingMaskIntoConstraints = NO;
    [self.hideLogButton setTitle:@"Hide" forState:UIControlStateNormal];
    self.hideLogButton.titleLabel.font = [UIFont boldSystemFontOfSize:13];
    [self.hideLogButton addTarget:self action:@selector(hideLogTapped) forControlEvents:UIControlEventTouchUpInside];
    self.hideLogButton.hidden = MxxAlwaysShowLogEnabled();
    [self.diagnosticsPanel.contentView addSubview:self.hideLogButton];

    self.logTextView = [[UITextView alloc] init];
    self.logTextView.translatesAutoresizingMaskIntoConstraints = NO;
    self.logTextView.backgroundColor = UIColor.clearColor;
    self.logTextView.textColor = UIColor.whiteColor;
    self.logTextView.font = [UIFont monospacedSystemFontOfSize:11 weight:UIFontWeightRegular];
    self.logTextView.editable = NO;
    self.logTextView.selectable = YES;
    self.logTextView.alwaysBounceVertical = YES;
    self.logTextView.text = @"Waiting for runtime log…";
    [self.diagnosticsPanel.contentView addSubview:self.logTextView];

    [NSLayoutConstraint activateConstraints:@[
        [self.diagnosticsPanel.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:12],
        [self.diagnosticsPanel.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-12],
        [self.diagnosticsPanel.bottomAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.bottomAnchor constant:-12],
        [self.diagnosticsPanel.heightAnchor constraintLessThanOrEqualToConstant:300],
        [self.diagnosticsPanel.heightAnchor constraintGreaterThanOrEqualToConstant:180],

        [title.leadingAnchor constraintEqualToAnchor:self.diagnosticsPanel.contentView.leadingAnchor constant:12],
        [title.topAnchor constraintEqualToAnchor:self.diagnosticsPanel.contentView.topAnchor constant:10],

        [self.hideLogButton.trailingAnchor constraintEqualToAnchor:self.diagnosticsPanel.contentView.trailingAnchor constant:-12],
        [self.hideLogButton.centerYAnchor constraintEqualToAnchor:title.centerYAnchor],
        [self.logCopyButton.trailingAnchor constraintEqualToAnchor:self.hideLogButton.leadingAnchor constant:-8],
        [self.logCopyButton.centerYAnchor constraintEqualToAnchor:title.centerYAnchor],

        [self.logTextView.leadingAnchor constraintEqualToAnchor:self.diagnosticsPanel.contentView.leadingAnchor constant:8],
        [self.logTextView.trailingAnchor constraintEqualToAnchor:self.diagnosticsPanel.contentView.trailingAnchor constant:-8],
        [self.logTextView.topAnchor constraintEqualToAnchor:title.bottomAnchor constant:6],
        [self.logTextView.bottomAnchor constraintEqualToAnchor:self.diagnosticsPanel.contentView.bottomAnchor constant:-8]
    ]];
}

- (void)setupTapHandler {
    UITapGestureRecognizer *tap = [[UITapGestureRecognizer alloc] initWithTarget:self action:@selector(handleTap:)];
    tap.cancelsTouchesInView = NO;
    [self.metalView addGestureRecognizer:tap];

    UITapGestureRecognizer *hudTap = [[UITapGestureRecognizer alloc]
        initWithTarget:self action:@selector(toggleRuntimeHUD:)];
    hudTap.numberOfTouchesRequired = 2;
    hudTap.numberOfTapsRequired = 2;
    hudTap.cancelsTouchesInView = NO;
    [self.view addGestureRecognizer:hudTap];
}

- (void)enterImmersiveGuestMode {
    self.statusPanel.hidden = YES;

    if (!MxxAlwaysShowLogEnabled()) {
        self.diagnosticsPanel.hidden = YES;
    }

    [self setNeedsStatusBarAppearanceUpdate];
    [self setNeedsUpdateOfHomeIndicatorAutoHidden];
    [self setNeedsUpdateOfScreenEdgesDeferringSystemGestures];

    [self appendRuntimeLogLevel:WG_LOG_INFO
                           tag:@"MxxHub"
                       message:@"V38 IMMERSIVE MODE: guest Metal view is edge-to-edge; two-finger double-tap toggles MxxHub HUD"];
}

- (void)toggleRuntimeHUD:(UITapGestureRecognizer *)gesture {
    (void)gesture;

    BOOL show = self.statusPanel.hidden;
    self.statusPanel.hidden = !show;

    if (!show && !MxxAlwaysShowLogEnabled()) {
        self.diagnosticsPanel.hidden = YES;
    }

    [self writeHostBreadcrumb:
        show ? @"V38 runtime HUD shown by two-finger double-tap"
             : @"V38 runtime HUD hidden by two-finger double-tap"];
}

- (void)startDisplayLink {
    self.displayLink = [CADisplayLink displayLinkWithTarget:self selector:@selector(renderFrame:)];
    self.displayLink.preferredFrameRateRange = CAFrameRateRangeMake(30, 120, 60);
    [self.displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
}

- (void)scheduleBootWatchdog {
    __weak MxxWineRuntimeViewController *weakSelf = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(20.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        __strong MxxWineRuntimeViewController *selfRef = weakSelf;
        if (!selfRef || selfRef.sawGuestWindow || selfRef.watchdogTriggered) return;
        selfRef.watchdogTriggered = YES;
        if (selfRef.bootFatalError) {
            [selfRef appendRuntimeLogLevel:WG_LOG_WARN
                                      tag:@"MxxHub"
                                  message:[NSString stringWithFormat:@"BOOT WATCHDOG: boot already stopped on an error; last checkpoint=%ld/5. Open Log for the blocker.", (long)selfRef.bootCheckpoint]];
            [selfRef setStatus:@"BOOT STOPPED — runtime error. Open Log for the exact blocker."];
        } else {
            [selfRef appendRuntimeLogLevel:WG_LOG_WARN
                                      tag:@"MxxHub"
                                  message:[NSString stringWithFormat:@"BOOT WATCHDOG: no guest window after 20s; last checkpoint=%ld/5. Engine is still running for diagnostics.", (long)selfRef.bootCheckpoint]];
            [selfRef setStatus:@"Still booting — no guest window after 20s. Open Log for the exact blocker."];
        }
        [selfRef showDiagnostics];
        [selfRef saveRuntimeLog];
    });
}

- (void)startEngine {
    if (self.runtimeClosing || !self.engineStarted) return;
    wg_log_init();
    wg_log_set_level(WG_LOG_DEBUG);
    wg_log_set_callback(MxxRuntimeLogCallback, (__bridge void *)self);
    [self appendRuntimeLogLevel:WG_LOG_INFO tag:@"MxxHub" message:[NSString stringWithFormat:@"Launching %@", self.executablePath.lastPathComponent]];

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
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
        [self writeHostBreadcrumb:@"V20 after wg_engine_create — entering wg_engine_init"];
        if (!self->_engine || !wg_engine_init(self->_engine)) {
            [self writeHostBreadcrumb:@"V20 wg_engine_init FAILED"];
            dispatch_async(dispatch_get_main_queue(), ^{
                [self setStatus:@"Runtime initialization failed"];
                [self showDiagnostics];
                [self saveRuntimeLog];
            });
            return;
        }

        [self writeHostBreadcrumb:@"V20 wg_engine_init OK — before wg_engine_load_pe"];
        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:@"Hollow Knight boot: mapping EXE + UnityPlayer…"]; });
        bool loaded = wg_engine_load_pe(self->_engine, self.executablePath.UTF8String);
        if (!loaded) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self setStatus:@"PE load failed — see compatibility log"];
                [self showDiagnostics];
                [self saveRuntimeLog];
            });
            return;
        }

        [self writeHostBreadcrumb:@"V20 PE load OK — before wg_engine_run"];
        bool started = wg_engine_run(self->_engine);
        if (!started) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self setStatus:@"Execution could not start — see compatibility log"];
                [self showDiagnostics];
                [self saveRuntimeLog];
            });
            return;
        }

        [self writeHostBreadcrumb:@"V20 wg_engine_run OK — starting engine thread"];
        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:@"Hollow Knight boot chain running — waiting for Unity window…"]; });
        [self startEngineThread];
    });
}

- (void)startEngineThread {
    _engineThreadRunning = YES;
    self.engineThread = [[NSThread alloc] initWithTarget:self selector:@selector(engineLoop) object:nil];
    self.engineThread.stackSize = 4 * 1024 * 1024;
    self.engineThread.qualityOfService = NSQualityOfServiceUserInteractive;
    self.engineThread.name = @"MxxHub-WindowsRuntime";
    [self.engineThread start];
    [self writeHostBreadcrumb:@"V20 engine NSThread started"];
}

- (void)engineLoop {
    @autoreleasepool {
        while (_engineThreadRunning && _engine) {
            WGEngineState state = wg_engine_get_state(_engine);
            if (state == WG_ENGINE_RUNNING) {
                wg_engine_tick(_engine);
            } else if (state == WG_ENGINE_PAUSED) {
                wg_engine_tick(_engine);
                usleep(8000);
            } else {
                dispatch_async(dispatch_get_main_queue(), ^{
                    NSString *message = nil;
                    if (state == WG_ENGINE_STOPPED) {
                        message = @"Windows program exited";
                    } else {
                        NSString *detail = self.lastDiagnosticLine;
                        if (detail.length > 0) {
                            message = [NSString stringWithFormat:@"Runtime stopped\n%@", detail];
                        } else {
                            message = @"Runtime stopped — inspect compatibility log";
                        }
                        [self showDiagnostics];
                        [self saveCrashSnapshotWithReason:
                            [NSString stringWithFormat:@"V21 engine state stopped unexpectedly (%ld)", (long)state]];
                    }
                    [self setStatus:message];
                    [self saveRuntimeLog];
                });
                break;
            }
        }
    }
}

- (void)appendRuntimeLogLevel:(WGLogLevel)level tag:(NSString *)tag message:(NSString *)message {
    NSString *line = [NSString stringWithFormat:@"[%@][%@] %@", MxxLogLevelName(level), tag ?: @"runtime", message ?: @""];

    NSString *bootProbe = line.lowercaseString;
    NSInteger newCheckpoint = self.bootCheckpoint;
    if ([bootProbe containsString:@"checkpoint 1/4"]) newCheckpoint = MAX(newCheckpoint, 1);
    if ([bootProbe containsString:@"checkpoint 2/4"]) newCheckpoint = MAX(newCheckpoint, 2);
    if ([bootProbe containsString:@"checkpoint 3/4"]) newCheckpoint = MAX(newCheckpoint, 3);
    if ([bootProbe containsString:@"checkpoint 4/4"]) newCheckpoint = MAX(newCheckpoint, 4);
    if (newCheckpoint != self.bootCheckpoint) {
        self.bootCheckpoint = newCheckpoint;
        [self setStatus:[NSString stringWithFormat:@"Hollow Knight boot checkpoint %ld reached", (long)newCheckpoint]];
    }

    [self.runtimeLogLines addObject:line];
    while (self.runtimeLogLines.count > 6000) {
        [self.runtimeLogLines removeObjectAtIndex:0];
    }

    NSString *lower = line.lowercaseString;
    BOOL fatalBootLine = level >= WG_LOG_ERROR &&
        ([lower containsString:@"failed to create blink vm"] ||
         [lower containsString:@"x64 load stopped"] ||
         [lower containsString:@"blink x64 vm required"] ||
         [lower containsString:@"runtime initialization failed"]);
    if (fatalBootLine) {
        self.bootFatalError = YES;
        [self setStatus:@"BOOT STOPPED — runtime error. Open Log for the exact blocker."];
    }

    BOOL looksImportant = level >= WG_LOG_ERROR ||
        [lower containsString:@"unsupported"] ||
        [lower containsString:@"unhandled"] ||
        [lower containsString:@"unknown api"] ||
        [lower containsString:@"missing api"] ||
        [lower containsString:@"import"] ||
        [lower containsString:@"thunk"] ||
        [lower containsString:@"exception"] ||
        [lower containsString:@"failed"] ||
        [lower containsString:@"blink unavailable"] ||
        [lower containsString:@"mxxhub hk boot"] ||
        [lower containsString:@"checkpoint"] ||
        [lower containsString:@"unityplayer"] ||
        [lower containsString:@"falling back"] ||
        [lower containsString:@"error"];
    if (looksImportant) {
        self.lastDiagnosticLine = line;
    }

    BOOL v21CrashLine =
        level == WG_LOG_FATAL ||
        fatalBootLine ||
        [lower containsString:@"crash at rip="] ||
        [lower containsString:@"sigsegv"] ||
        [lower containsString:@"kern_invalid_address"] ||
        [lower containsString:@"runtime crashed"];

    if (v21CrashLine) {
        [self saveCrashSnapshotWithReason:line];
        if (MxxAlwaysShowLogEnabled()) [self showDiagnostics];
    }

    static NSUInteger v20PersistCounter = 0;
    v20PersistCounter++;
    if ((v20PersistCounter % 20) == 0 || level >= WG_LOG_ERROR) {
        [self saveRuntimeLog];
    }

    self.logTextView.text = [self.runtimeLogLines componentsJoinedByString:@"\n"];
    if (self.logTextView.text.length > 0) {
        NSRange bottom = NSMakeRange(self.logTextView.text.length - 1, 1);
        [self.logTextView scrollRangeToVisible:bottom];
    }
}

- (void)writeHostBreadcrumb:(NSString *)message {
    NSArray<NSURL *> *docs = [NSFileManager.defaultManager
        URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask];
    NSURL *docsURL = docs.firstObject;
    if (!docsURL) return;

    NSURL *dir = [docsURL URLByAppendingPathComponent:@"MxxHubLogs" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:dir
                           withIntermediateDirectories:YES
                                            attributes:nil error:nil];

    NSString *readme = @"MxxHub diagnostic files\n\n"
        "Files → Browse → On My iPad/iPhone → MxxHub → MxxHubLogs\n\n"
        "Send host-latest.txt, runtime-latest.log and crash-latest.log.\n";
    NSURL *folderReadme = [dir URLByAppendingPathComponent:@"README.txt"];
    if (![NSFileManager.defaultManager fileExistsAtPath:folderReadme.path]) {
        [readme writeToURL:folderReadme atomically:YES encoding:NSUTF8StringEncoding error:nil];
    }
    NSURL *rootReadme = [docsURL URLByAppendingPathComponent:@"MxxHub-Logs-README.txt"];
    if (![NSFileManager.defaultManager fileExistsAtPath:rootReadme.path]) {
        [readme writeToURL:rootReadme atomically:YES encoding:NSUTF8StringEncoding error:nil];
    }

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

- (void)showDiagnostics {
    self.diagnosticsPanel.hidden = NO;
}

- (void)logTapped {
    if (MxxAlwaysShowLogEnabled()) {
        self.diagnosticsPanel.hidden = NO;
        return;
    }
    self.diagnosticsPanel.hidden = !self.diagnosticsPanel.hidden;
}

- (void)hideLogTapped {
    if (MxxAlwaysShowLogEnabled()) {
        self.diagnosticsPanel.hidden = NO;
        return;
    }
    self.diagnosticsPanel.hidden = YES;
}

- (void)copyLogTapped {
    NSString *text = [self.runtimeLogLines componentsJoinedByString:@"\n"];
    if (text.length == 0) text = @"MxxHub: no runtime log captured.";
    UIPasteboard.generalPasteboard.string = text;
    [self.logCopyButton setTitle:@"Copied ✓" forState:UIControlStateNormal];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.2 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        [self.logCopyButton setTitle:@"Copy Log" forState:UIControlStateNormal];
    });
}

- (void)saveRuntimeLog {
    if (self.runtimeLogLines.count == 0) return;
    NSString *text = [self.runtimeLogLines componentsJoinedByString:@"\n"];
    NSArray<NSURL *> *docs = [NSFileManager.defaultManager URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask];
    NSURL *docsURL = docs.firstObject;
    if (!docsURL) return;

    NSURL *dir = [docsURL URLByAppendingPathComponent:@"MxxHubLogs" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:dir withIntermediateDirectories:YES attributes:nil error:nil];

    NSString *base = self.executablePath.lastPathComponent.stringByDeletingPathExtension;
    if (base.length == 0) base = @"runtime";
    NSCharacterSet *unsafe = [[NSCharacterSet alphanumericCharacterSet] invertedSet];
    base = [[base componentsSeparatedByCharactersInSet:unsafe] componentsJoinedByString:@"_"];
    NSString *fileName = [NSString stringWithFormat:@"%@-latest.log", base];
    NSURL *fileURL = [dir URLByAppendingPathComponent:fileName];
    if ([text writeToURL:fileURL atomically:YES encoding:NSUTF8StringEncoding error:nil]) {
        self.savedLogPath = fileURL.path;
    }

    NSURL *liveURL = [dir URLByAppendingPathComponent:@"runtime-latest.log"];
    [text writeToURL:liveURL atomically:YES encoding:NSUTF8StringEncoding error:nil];
}

- (void)saveCrashSnapshotWithReason:(NSString *)reason {
    [self saveRuntimeLog];

    NSArray<NSURL *> *docs = [NSFileManager.defaultManager
        URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask];
    NSURL *docsURL = docs.firstObject;
    if (!docsURL) return;

    NSURL *dir = [docsURL URLByAppendingPathComponent:@"MxxHubLogs" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:dir
                           withIntermediateDirectories:YES
                                            attributes:nil error:nil];

    NSMutableString *snapshot = [NSMutableString string];
    [snapshot appendString:@"MxxHub v0.4.9.57 V48 crash snapshot\n"];
    [snapshot appendFormat:@"Game: %@\n", self.executablePath.lastPathComponent ?: @"<unknown>"];
    [snapshot appendFormat:@"Reason: %@\n", reason ?: @"runtime stopped unexpectedly"];
    [snapshot appendFormat:@"Checkpoint: %ld/5\n\n", (long)self.bootCheckpoint];

    NSURL *hostURL = [dir URLByAppendingPathComponent:@"host-latest.txt"];
    NSString *host = [NSString stringWithContentsOfURL:hostURL encoding:NSUTF8StringEncoding error:nil];
    if (host.length > 0) {
        [snapshot appendString:@"=== HOST BREADCRUMBS ===\n"];
        [snapshot appendString:host];
        [snapshot appendString:@"\n"];
    }

    [snapshot appendString:@"=== RUNTIME LOG ===\n"];
    [snapshot appendString:[self.runtimeLogLines componentsJoinedByString:@"\n"]];
    [snapshot appendString:@"\n"];

    NSURL *latest = [dir URLByAppendingPathComponent:@"crash-latest.log"];
    [snapshot writeToURL:latest atomically:YES encoding:NSUTF8StringEncoding error:nil];

    NSDateFormatter *formatter = [[NSDateFormatter alloc] init];
    formatter.locale = [NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];
    formatter.dateFormat = @"yyyyMMdd-HHmmss";
    NSString *stamp = [formatter stringFromDate:NSDate.date];
    NSURL *timestamped = [dir URLByAppendingPathComponent:
        [NSString stringWithFormat:@"crash-%@.log", stamp]];
    [snapshot writeToURL:timestamped atomically:YES encoding:NSUTF8StringEncoding error:nil];

    if (MxxCopyLogOnCrashEnabled()) {
        UIPasteboard.generalPasteboard.string = snapshot;
    }

    [self writeHostBreadcrumb:[NSString stringWithFormat:
        @"V21 crash snapshot saved: %@", reason ?: @"unknown"]];
}

- (void)renderFrame:(CADisplayLink *)link {
    (void)link;
    if (!self.commandQueue || !self.compositor) {
        id<MTLDevice> device = self.metalView.metalLayer.device;
        if (!device) {
            device = MTLCreateSystemDefaultDevice();
            if (device) self.metalView.metalLayer.device = device;
        }
        if (device) {
            if (!self.commandQueue) self.commandQueue = [device newCommandQueue];
            if (!self.compositor) self.compositor = [[WGCompositor alloc] initWithDevice:device];
            if (self.commandQueue && self.compositor) {
                [self writeHostBreadcrumb:@"V47 METAL COLD-START RECOVERY: device/queue/compositor ready"];
            }
        }
        if (!self.commandQueue || !self.compositor) return;
    }
    id<CAMetalDrawable> drawable = [self.metalView.metalLayer nextDrawable];
    if (!drawable) return;

    if (self.engineStartScheduled && !self.engineStarted && !self.runtimeClosing) {
        BOOL attached = (self.view.window != nil) && (self.metalView.window != nil);
        CFTimeInterval elapsed = self.v38ViewAppearedAt > 0
            ? (CACurrentMediaTime() - self.v38ViewAppearedAt) : 0;
        if (attached) self.v38ReadyDrawableCount++;

        if (attached && self.v38ReadyDrawableCount >= 3 && elapsed >= 0.35) {
            self.engineStartScheduled = NO;
            self.engineStarted = YES;
            [self writeHostBreadcrumb:[NSString stringWithFormat:
                @"V38 Metal gate passed — drawables=%ld elapsed=%.3fs; starting engine",
                (long)self.v38ReadyDrawableCount, elapsed]];
            [self appendRuntimeLogLevel:WG_LOG_INFO tag:@"MxxHub"
                                message:@"V38 START GATE: fullscreen UIWindow + 3 Metal drawables ready"];
            [self startEngine];
            [self scheduleBootWatchdog];
        }
    }

    if (wg_wm_visible_count() > 0 && (!_engine || !wg_engine_dialog_active(_engine))) {
        if (!self.sawGuestWindow) {
            self.sawGuestWindow = YES;
            self.bootCheckpoint = 5;
            [self appendRuntimeLogLevel:WG_LOG_INFO tag:@"MxxHub" message:@"BOOT CHECKPOINT 5/5: first non-dialog guest window is visible on the Metal compositor"];
            [self setStatus:@"Guest window created — waiting for the real Hollow Knight menu…"];
            [self enterImmersiveGuestMode];
        }
        [self.compositor renderWindowsToDrawable:drawable
                                     commandQueue:self.commandQueue
                                       screenSize:self.metalView.metalLayer.drawableSize];
    } else {
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = drawable.texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = MTLClearColorMake(0.025, 0.03, 0.035, 1.0);
        id<MTLCommandBuffer> buffer = [self.commandQueue commandBuffer];
        id<MTLRenderCommandEncoder> encoder = [buffer renderCommandEncoderWithDescriptor:pass];
        [encoder endEncoding];
        [buffer presentDrawable:drawable];
        [buffer commit];
    }
}

- (void)handleTap:(UITapGestureRecognizer *)gesture {
    if (!_engine || !wg_engine_dialog_active(_engine)) return;
    CGPoint point = [gesture locationInView:self.metalView];
    CGSize drawable = self.metalView.metalLayer.drawableSize;
    CGSize bounds = self.metalView.bounds.size;
    if (bounds.width <= 0 || bounds.height <= 0) return;

    float px = point.x * (drawable.width / bounds.width);
    float py = point.y * (drawable.height / bounds.height);
    float scale = fminf(drawable.width / 800.0f, drawable.height / 600.0f);
    if (scale <= 0) return;
    float offsetX = (drawable.width - 800.0f * scale) * 0.5f;
    float offsetY = (drawable.height - 600.0f * scale) * 0.5f;
    int vx = (int)((px - offsetX) / scale);
    int vy = (int)((py - offsetY) / scale);
    uint32_t control = wg_engine_hit_test(_engine, vx, vy);
    if (control) wg_engine_dialog_command(_engine, control);
}

- (void)setStatus:(NSString *)text {
    self.statusLabel.text = [NSString stringWithFormat:
        @"MxxHub Windows Runtime v0.4.9.57 V48\nBOOT %ld/5 • x64 Blink correctness mode • JIT OFF\n%@\n%@",
        (long)self.bootCheckpoint, text ?: @"", self.executablePath.lastPathComponent];
}

- (void)closeTapped {
    if (self.runtimeClosing) return;
    self.runtimeClosing = YES;
    [self saveRuntimeLog];

    [[NSNotificationCenter defaultCenter]
        postNotificationName:@"MxxHubRuntimeRequestedDismiss"
                      object:nil];

    [self shutdownEngine];
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (void)shutdownEngine {
    if (self.runtimeClosing == NO) self.runtimeClosing = YES;
    [self writeHostBreadcrumb:@"V38 shutdownEngine entered"];
    _engineThreadRunning = NO;
    [self.displayLink invalidate];
    self.displayLink = nil;
    wg_log_set_callback(NULL, NULL);
    if (_engine) {
        wg_engine_stop(_engine);
        [NSThread sleepForTimeInterval:0.02];
        [self writeHostBreadcrumb:@"V20 before wg_engine_destroy"];
        wg_engine_destroy(_engine);
        _engine = NULL;
        [self writeHostBreadcrumb:@"V20 after wg_engine_destroy"];
    }
    UIApplication.sharedApplication.idleTimerDisabled = NO;
}

@end
