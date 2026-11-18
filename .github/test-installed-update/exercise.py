"""Exercise the packaged AppFlowy 0.15.0 update UI on a disposable macOS runner."""
import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
BROWSERS = {
    'com.apple.safari', 'com.google.chrome', 'org.mozilla.firefox',
    'com.microsoft.edgemac', 'com.brave.browser', 'com.operasoftware.opera',
    'company.thebrowser.browser', 'com.vivaldi.vivaldi',
}


def command(*args):
    return subprocess.check_output([str(a) for a in args], text=True)


def version(app):
    with (app / 'Contents/Info.plist').open('rb') as f:
        return plistlib.load(f)['CFBundleShortVersionString']


def is_browser(app, default_path):
    return bool(default_path and app.get('path') == default_path) or (
        app.get('bundleID', app.get('bundle', '')).lower() in BROWSERS
    )


def select(lines, allowed):
    """Find visible standalone labels; prefer the dialog action at bottom right."""
    matches = [line for line in lines if line['text'].strip().casefold() in allowed]
    return max(matches, key=lambda line: (line['y'], line['x']), default=None)


def action(lines, stage):
    text = '\n'.join(row['text'].casefold() for row in lines)
    if stage == 'start':
        if '0.15.0' in text and '0.15.1' in text:
            return 'select-release', select(lines, {'update'})
        if 'new version available' in text:
            return 'open-update', select(lines, {'update'})
        if 'check for updates' in text:
            return 'check-updates', select(lines, {'check for updates'})
        if 'welcome' in text:
            return 'create-local-workspace', select(lines, {'anonymous', "let's go", 'get started'})
    elif stage in ('native', 'relaunch'):
        restart = select(lines, {'install and relaunch', 'install & relaunch', 'relaunch and install'})
        if restart:
            return 'install-and-relaunch', restart
        if stage == 'native' and ('software update' in text or '0.15.1' in text):
            return 'confirm-native-update', select(lines, {'install update', 'install'})
    return 'wait', None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Use a disposable GitHub-hosted macOS GUI session.')
    app, evidence = args.app.resolve(), args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    gui = evidence / 'appflowy-ui'
    subprocess.run(['swiftc', HERE / 'gui.swift', '-o', gui], check=True)
    if version(app) != '0.15.0':
        raise RuntimeError('Expected the official 0.15.0 app as the starting installation.')
    before = json.loads(command(gui, 'apps'))
    (evidence / 'initial-apps.json').write_text(json.dumps(before, indent=2))
    if any(a['path'] == str(app) for a in before['apps']):
        raise RuntimeError('Start with the staged app closed so its baseline launch can be recorded.')
    if not before['canPostEvents']:
        raise RuntimeError('Quartz event posting is unavailable for this runner session.')
    browser = before['browser']
    if not browser or any(is_browser(a, browser) for a in before['apps']):
        raise RuntimeError('Need a registered default HTTPS handler and a browser-free GUI baseline.')
    events_file = evidence / 'workspace-events.jsonl'
    with events_file.open('w') as out, (evidence / 'monitor-stderr.txt').open('w') as err:
        monitor = subprocess.Popen(['/usr/bin/osascript', '-l', 'JavaScript', HERE / 'monitor-upgrade.js', '900'], stdout=out, stderr=err)
        try:
            run(app, evidence, gui, browser, events_file, monitor)
        finally:
            monitor.terminate()
            monitor.wait(timeout=15)


def run(app, evidence, gui, browser, events_file, monitor):
    def events():
        if monitor.poll() is not None:
            raise RuntimeError('Workspace monitor terminated before verification.')
        # A concurrent writer may be partway through its final JSONL record.
        contents = events_file.read_text().split('\n')[:-1]
        return [json.loads(line) for line in contents if line]

    wait_until = time.monotonic() + 20
    while not any(e['event'] == 'ready' for e in events()):
        if time.monotonic() > wait_until:
            raise RuntimeError('Workspace monitor did not report readiness.')
        time.sleep(0.3)
    subprocess.run(['open', '-n', app], check=True)
    start = time.monotonic()
    stage = 'start'
    current = evidence / 'latest.png'
    old_pid = None
    last_tap = {}
    native_consent = False
    settings_sent = False
    scrolls = 0
    while time.monotonic() - start < 600:
        observed = events()
        if any(e['event'] == 'monitor-error' for e in observed):
            raise RuntimeError('Workspace monitor reported an incomplete observation.')
        if any(is_browser(e.get('app', {}), browser) for e in observed if e['event'] in ('activate', 'launch')):
            raise RuntimeError('A browser was launched or activated during the test.')
        apps = json.loads(command(gui, 'apps'))
        if any(is_browser(a, browser) for a in apps['apps']):
            raise RuntimeError('A browser is running after the update attempt.')
        instances = [a for a in apps['apps'] if a['path'] == str(app)]
        if instances and old_pid is None:
            old_pid = instances[0]['pid']
        replaced = [a for a in instances if old_pid and a['pid'] != old_pid]
        if stage in ('native', 'relaunch') and replaced and version(app) == '0.15.1':
            if not native_consent:
                raise RuntimeError('App was replaced without recorded native updater consent.')
            target_pid = replaced[0]['pid']
            time.sleep(15)
            final = json.loads(command(gui, 'apps'))
            assert any(a['pid'] == target_pid and a['path'] == str(app) for a in final['apps'])
            assert not any(a['pid'] == old_pid or is_browser(a, browser) for a in final['apps'])
            completed = events()
            if any(is_browser(e.get('app', {}), browser) or e['event'] == 'monitor-error'
                   for e in completed if e['event'] in ('activate', 'launch', 'monitor-error')):
                raise RuntimeError('A browser opened or the workspace monitor lost observation during relaunch.')
            lifecycle = {(e['event'], e.get('app', {}).get('pid')) for e in completed}
            if not {('launch', old_pid), ('terminate', old_pid), ('launch', target_pid)} <= lifecycle:
                raise RuntimeError('Independent workspace observer did not confirm both launches and original app exit.')
            subprocess.run(['codesign', '--verify', '--deep', '--strict', app], check=True)
            subprocess.run(['screencapture', '-x', '-m', str(evidence / 'updated-0.15.1.png')], check=True)
            result = dict(start_version='0.15.0', end_version=version(app), old_pid=old_pid, new_pid=target_pid,
                          native_consent=native_consent, new_process_survived_seconds=15, browser_launches=0, app_path=str(app))
            (evidence / 'result.json').write_text(json.dumps(result, indent=2))
            print(json.dumps(result), flush=True)
            return
        subprocess.run(['screencapture', '-x', '-m', str(current)], check=True)
        lines = json.loads(command(gui, 'scan', current))
        (evidence / 'latest-ocr.json').write_text(json.dumps(lines, indent=2))
        if int(time.monotonic() - start) > 140 and stage == 'start' and 'select-release' not in last_tap:
            print('Latest window labels:', [row['text'] for row in lines], flush=True)
            raise RuntimeError('Could not reach the versioned update dialog; inspect screenshots.')
        front = apps['front']
        name, label = action(lines, stage)
        elapsed = int(time.monotonic()-start)
        foreground_ok = front.get('path', '').startswith(str(app)) or 'appflowy' in front.get('name', '').lower()
        if not label and stage == 'start' and foreground_ok:
            words = '\n'.join(row['text'].casefold() for row in lines)
            guest_tap = last_tap.get('create-local-workspace')
            if not settings_sent and (
                (guest_tap is not None and elapsed > guest_tap+25 and 'welcome to appflowy' not in words)
                or elapsed > 40 and any(s in words for s in ('getting started', 'new page', 'favorites'))
            ):
                shutil.copy(current, evidence / 'before-open-settings.png')
                subprocess.run([gui, 'settings'], check=True)
                settings_sent = True
                print('Open Settings via Cmd+,', flush=True)
            elif settings_sent and scrolls < 6 and elapsed - last_tap.get('scroll', -999) > 3:
                anchors = [row for row in lines if row['text'].strip().casefold() in
                           ('account & app', 'about appflowy', 'language', 'date format', 'time format', 'login')]
                if anchors:
                    anchor = max(anchors, key=lambda row: row['x'])
                    subprocess.run([gui, 'scroll', str(anchor['x']), str(min(0.87, anchor['y']+0.08))], check=True)
                    last_tap['scroll'] = elapsed
                    scrolls += 1
        if label and foreground_ok:
            if elapsed - last_tap.get(name, -999) >= (12 if name in ('open-update', 'check-updates') else 5):
                last_tap[name] = elapsed
                screenshot = f'{elapsed:03d}-{name}'
                shutil.copy(current, evidence / (screenshot + '.png'))
                with (evidence / 'actions.jsonl').open('a') as log:
                    log.write(json.dumps(dict(action=name, label=label, elapsed=elapsed, front=front, time=datetime.datetime.now(datetime.timezone.utc).isoformat()))+'\n')
                print('Click', name, label['text'], flush=True)
                subprocess.run([gui, 'click', str(label['x']), str(label['y'])], check=True)
                if name == 'select-release':
                    stage = 'native'
                elif name in ('confirm-native-update', 'install-and-relaunch'):
                    native_consent = True
                    stage = 'relaunch'
        time.sleep(1.5)
    raise RuntimeError(f'Timed out waiting for update, stage={stage}; inspect latest.png, OCR and action events.')


if __name__ == '__main__':
    main()
