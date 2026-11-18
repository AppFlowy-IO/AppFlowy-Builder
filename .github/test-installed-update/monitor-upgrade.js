// Run in the logged-in guest's GUI session with osascript -l JavaScript.
ObjC.import('AppKit');
ObjC.import('Foundation');

function run(argv) {
  const duration = Number(argv[0] || 900);
  if (!isFinite(duration) || duration <= 0) throw Error('Expected a positive duration in seconds');
  const ws = $.NSWorkspace.sharedWorkspace;
  const center = ws.notificationCenter;
  function string(value) {
    return value && !value.isNil() ? String(ObjC.unwrap(value)) : '';
  }
  function write(message) {
    message.time = new Date().toISOString();
    const json = $.NSString.stringWithString(JSON.stringify(message) + '\n');
    $.NSFileHandle.fileHandleWithStandardOutput.writeData(json.dataUsingEncoding($.NSUTF8StringEncoding));
  }
  function describe(app) {
    if (!app || app.isNil()) return {};
    const url = app.bundleURL;
    const path = url && !url.isNil() ? string(url.path) : '';
    const result = {name: string(app.localizedName), bundleID: string(app.bundleIdentifier), pid: Number(app.processIdentifier), path: path};
    if (/appflowy/i.test(result.name) && path) {
      const info = $.NSDictionary.dictionaryWithContentsOfFile(path + '/Contents/Info.plist');
      if (!info.isNil()) result.versionOnDisk = string(info.objectForKey('CFBundleShortVersionString'));
    }
    return result;
  }
  const handler = ws.URLForApplicationToOpenURL($.NSURL.URLWithString('https://example.invalid/'));
  write({event: 'monitor-start', defaultHTTPSHandler: handler.isNil() ? '' : string(handler.path), durationSeconds: duration});
  const subscriptions = [];
  const observer = function(event, notification) {
    subscriptions.push(center.addObserverForNameObjectQueueUsingBlock(notification, $(), $.NSOperationQueue.mainQueue, function(note) {
      try {
        const app = note.userInfo.objectForKey($.NSWorkspaceApplicationKey);
        write({event: event, app: describe(app)});
      } catch (error) { write({event: 'monitor-error', detail: String(error)}); }
    }));
  };
  observer('launch', $.NSWorkspaceDidLaunchApplicationNotification);
  observer('activate', $.NSWorkspaceDidActivateApplicationNotification);
  observer('terminate', $.NSWorkspaceDidTerminateApplicationNotification);
  const apps = ws.runningApplications;
  for (let i = 0; i < Number(apps.count); i++) {
    const app = apps.objectAtIndex(i);
    if (Number(app.activationPolicy) === 0) write({event: 'initial-gui-app', app: describe(app)});
  }
  write({event: 'initial-frontmost', app: describe(ws.frontmostApplication)});
  write({event: 'ready'});
  const deadline = Date.now() + duration * 1000;
  while (Date.now() < deadline) {
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(Math.min(1, (deadline - Date.now())/1000)));
  }
  subscriptions.forEach(function(token) { center.removeObserver(token); });
  write({event: 'monitor-end'});
}
