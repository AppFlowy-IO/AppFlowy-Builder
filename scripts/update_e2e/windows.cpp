#include <windows.h>
#include <winsparkle.h>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <string>

#ifndef FIXTURE_VERSION
#define FIXTURE_VERSION "1.0.0"
#endif

namespace fs = std::filesystem;
static fs::path case_path;
static HANDLE finished;
static std::mutex state_mutex;
static std::map<std::string, std::wstring> preferences;

static std::string read(const fs::path &path) {
  std::ifstream file(path);
  return std::string(std::istreambuf_iterator<char>(file), {});
}

static void event(const char *name) {
  std::lock_guard<std::mutex> lock(state_mutex);
  std::ofstream file(case_path / "events.jsonl", std::ios::app);
  file << "{\"event\":\"" << name << "\",\"version\":\"" << FIXTURE_VERSION
       << "\",\"pid\":" << GetCurrentProcessId() << "}\n";
}

static int __cdecl config_read(const char *name, wchar_t *out, size_t size, void *) {
  std::lock_guard<std::mutex> lock(state_mutex);
  auto it = preferences.find(name);
  return it != preferences.end() && wcscpy_s(out, size, it->second.c_str()) == 0;
}
static void __cdecl config_write(const char *name, const wchar_t *value, void *) {
  std::lock_guard<std::mutex> lock(state_mutex);
  preferences[name] = value;
}
static void __cdecl config_delete(const char *name, void *) {
  std::lock_guard<std::mutex> lock(state_mutex);
  preferences.erase(name);
}
static void __cdecl error() { event("error"); SetEvent(finished); }
static void __cdecl no_update() { event("no-update"); SetEvent(finished); }
static void __cdecl found() { event("found"); }
static int __cdecl can_shutdown() { return 1; }
static void __cdecl shutdown() { event("terminated"); SetEvent(finished); }
static int __cdecl installer(const wchar_t *) {
  event("installing");
  // Observe only. WinSparkle launches the verified installer itself, with the
  // silent Inno arguments in the isolated test feed, just as in production.
  return 0;
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
  wchar_t executable[32768];
  if (!GetModuleFileNameW(nullptr, executable, 32768)) return 2;
  case_path = fs::path(executable).parent_path().parent_path();
  event("launched");
  if (read(case_path / "target-version.txt") == FIXTURE_VERSION) {
    // The new executable, not the controller, reads the surviving test data.
    std::ofstream(case_path / "relaunch-data.txt") << read(case_path / "data.txt");
    event("relaunched");
    return 0;
  }
  finished = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (!finished) return 3;
  win_sparkle_config_methods_t config{config_read, config_write, config_delete, nullptr};
  win_sparkle_set_config_methods(&config);
  win_sparkle_set_app_details(L"AppFlowy CI", L"Update E2E", L"" FIXTURE_VERSION);
  win_sparkle_set_automatic_check_for_updates(0);
  const auto feed = read(case_path / "feed-url.txt");
  win_sparkle_set_appcast_url(feed.c_str());
  win_sparkle_set_error_callback(error);
  win_sparkle_set_did_not_find_update_callback(no_update);
  win_sparkle_set_did_find_update_callback(found);
  win_sparkle_set_can_shutdown_callback(can_shutdown);
  win_sparkle_set_shutdown_request_callback(shutdown);
  win_sparkle_set_user_run_installer_callback(installer);
  win_sparkle_init();
  win_sparkle_check_update_with_ui_and_install();
  DWORD result = WaitForSingleObject(finished, 180000);
  win_sparkle_cleanup();
  CloseHandle(finished);
  return result == WAIT_OBJECT_0 ? 0 : 4;
}
