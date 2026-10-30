#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>

// A disposable host for the real updater. All state is scoped to this test's
// bundle identifier and directory; it never opens the installed AppFlowy app.
static NSString *casePath;
static NSDictionary *config;

static void event(NSString *name, NSDictionary *details) {
  NSMutableDictionary *record = [details mutableCopy] ?: [NSMutableDictionary new];
  record[@"event"] = name;
  record[@"pid"] = @([[NSProcessInfo processInfo] processIdentifier]);
  NSData *data = [NSJSONSerialization dataWithJSONObject:record options:0 error:nil];
  NSString *path = [casePath stringByAppendingPathComponent:@"events.jsonl"];
  if (![[NSFileManager defaultManager] fileExistsAtPath:path]) {
    [[NSFileManager defaultManager] createFileAtPath:path contents:nil attributes:nil];
  }
  NSFileHandle *file = [NSFileHandle fileHandleForWritingAtPath:path];
  [file seekToEndOfFile];
  [file writeData:data];
  [file writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
  [file closeFile];
}

@interface Driver : NSObject <SPUUserDriver, NSApplicationDelegate>
@property(strong) SPUUpdater *updater;
@end

@implementation Driver
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
  casePath = [[[NSBundle mainBundle] bundlePath] stringByDeletingLastPathComponent].stringByDeletingLastPathComponent;
  NSData *data = [NSData dataWithContentsOfFile:[casePath stringByAppendingPathComponent:@"config.json"]];
  config = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
  NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
  NSString *marker = [NSString stringWithContentsOfFile:[casePath stringByAppendingPathComponent:@"data.txt"] encoding:NSUTF8StringEncoding error:nil];
  event(@"launched", @{@"version": version, @"compiled_version": @FIXTURE_VERSION, @"data": marker ?: @""});
  if ([version isEqual:config[@"version"]]) {
    event(@"relaunched", @{@"version": version, @"data": marker ?: @""});
    [NSApp terminate:nil];
    return;
  }
  self.updater = [[SPUUpdater alloc] initWithHostBundle:NSBundle.mainBundle applicationBundle:NSBundle.mainBundle userDriver:self delegate:nil];
  NSError *error = nil;
  if (![self.updater startUpdater:&error]) {
    [self showUpdaterError:error acknowledgement:^{}];
    return;
  }
  [self.updater checkForUpdates];
}
- (void)applicationWillTerminate:(NSNotification *)notification { event(@"terminated", @{}); }
- (void)showUpdatePermissionRequest:(SPUUpdatePermissionRequest *)request reply:(void (^)(SUUpdatePermissionResponse *))reply {
  reply([[SUUpdatePermissionResponse alloc] initWithAutomaticUpdateChecks:NO sendSystemProfile:NO]);
}
- (void)showUserInitiatedUpdateCheckWithCancellation:(void (^)(void))cancellation { event(@"checking", @{}); }
- (void)showUpdateFoundWithAppcastItem:(SUAppcastItem *)item state:(SPUUserUpdateState *)state reply:(void (^)(SPUUserUpdateChoice))reply {
  event(@"found", @{@"version": item.displayVersionString});
  if ([config[@"scenario"] isEqual:@"skip"]) {
    reply(SPUUserUpdateChoiceSkip);
    event(@"skipped", @{});
    [NSApp terminate:nil];
  } else {
    reply(SPUUserUpdateChoiceInstall);
  }
}
- (void)showUpdateReleaseNotesWithDownloadData:(SPUDownloadData *)data {}
- (void)showUpdateReleaseNotesFailedToDownloadWithError:(NSError *)error {}
- (void)showUpdateNotFoundWithError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
  [self showUpdaterError:error acknowledgement:acknowledgement];
}
- (void)showUpdaterError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
  NSMutableArray *errors = [NSMutableArray new];
  for (NSError *current = error; current; current = current.userInfo[NSUnderlyingErrorKey]) {
    [errors addObject:@{@"domain": current.domain, @"code": @(current.code), @"description": current.description}];
  }
  event(@"error", @{@"errors": errors});
  acknowledgement();
  [NSApp terminate:nil];
}
- (void)showDownloadInitiatedWithCancellation:(void (^)(void))cancellation { event(@"downloading", @{}); }
- (void)showDownloadDidReceiveExpectedContentLength:(uint64_t)length {}
- (void)showDownloadDidReceiveDataOfLength:(uint64_t)length {}
- (void)showDownloadDidStartExtractingUpdate { event(@"extracting", @{}); }
- (void)showExtractionReceivedProgress:(double)progress {}
- (void)showReadyToInstallAndRelaunch:(void (^)(SPUUserUpdateChoice))reply {
  event(@"installing", @{});
  reply(SPUUserUpdateChoiceInstall);
}
- (void)showInstallingUpdateWithApplicationTerminated:(BOOL)terminated retryTerminatingApplication:(void (^)(void))retry {}
- (void)showUpdateInstalledAndRelaunched:(BOOL)relaunched acknowledgement:(void (^)(void))acknowledgement { acknowledgement(); }
- (void)dismissUpdateInstallation {}
@end

int main(void) {
  @autoreleasepool {
    NSApplication *app = [NSApplication sharedApplication];
    Driver *driver = [Driver new];
    app.delegate = driver;
    [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [app run];
  }
  return 0;
}
