// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use tauri::Manager;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, TrayIconBuilder, TrayIconEvent};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[derive(Serialize)]
struct OpenAppResult {
    success: bool,
    message: String,
    method: String,
    app: String,
}

fn normalize_app_name(app_name: &str) -> String {
    app_name.trim().to_lowercase()
}

fn canonical_app_name(app_name: &str) -> String {
    let n = normalize_app_name(app_name);
    match n.as_str() {
        // Browsers
        "ff" | "mozilla" | "mozilla firefox" => "firefox".to_string(),
        "google chrome" | "chrome" => "chrome".to_string(),
        // Chat
        "discordapp" => "discord".to_string(),
        "whatsapp" | "whats" | "wasitlos" | "waitlos" | "waitlosapp" | "wasistlos" | "wasistlo" | "was ist los" => "waitlos".to_string(),
        // Terminal — platform-dependent
        #[cfg(target_os = "windows")]
        "terminal" | "term" | "consola" | "shell" => "cmd".to_string(),
        #[cfg(not(target_os = "windows"))]
        "terminal" | "term" | "consola" | "shell" | "alacritty" => "konsole".to_string(),
        // File manager — platform-dependent
        #[cfg(target_os = "windows")]
        "archivos" | "files" | "file manager" | "explorador" | "explorador de archivos" => "explorer".to_string(),
        #[cfg(not(target_os = "windows"))]
        "archivos" | "files" | "file manager" | "explorador" | "explorador de archivos" => "dolphin".to_string(),
        // Editor
        "editor" | "editor de texto" | "text editor" => "kate".to_string(),
        // VS Code
        "vscode" | "vs code" | "visual studio code" | "visual studio" => "code".to_string(),
        // Settings
        #[cfg(target_os = "windows")]
        "configuracion" | "configuración" | "settings" | "ajustes" | "system settings" | "preferencias" => "ms-settings".to_string(),
        #[cfg(not(target_os = "windows"))]
        "configuracion" | "configuración" | "settings" | "ajustes" | "system settings" | "preferencias" => "systemsettings".to_string(),
        // LibreOffice
        "office" | "libre office" | "writer" | "calc" | "impress" => "libreoffice".to_string(),
        // Gaming
        "valve" => "steam".to_string(),
        _ => n,
    }
}

fn web_app_url(app: &str) -> Option<String> {
    let url = match app {
        "instagram" => "https://instagram.com",
        "facebook" => "https://facebook.com",
        "twitter" => "https://twitter.com",
        "whatsapp" => "https://web.whatsapp.com",
        "telegram" => "https://web.telegram.org",
        "youtube" => "https://youtube.com",
        "youtube music" | "yt music" => "https://music.youtube.com",
        "tiktok" => "https://tiktok.com",
        "coursera" => "https://coursera.org",
        "cientifica_canvas" | "ucientifica" => "https://cientificavirtual.cientifica.edu.pe",
        "gmail" => "https://gmail.com",
        "github" => "https://github.com",
        "reddit" => "https://reddit.com",
        "twitch" => "https://twitch.tv",
        "netflix" => "https://netflix.com",
        "spotify" => "https://open.spotify.com",
        "prime video" | "primevideo" => "https://primevideo.com",
        "hbo" => "https://hbomax.com",
        "stackoverflow" | "stack overflow" => "https://stackoverflow.com",
        "pinterest" => "https://pinterest.com",
        _ => return None,
    };
    Some(url.to_string())
}

fn launch_with_web(app: &str, url: &str) -> Option<OpenAppResult> {
    // Open URL in default browser (platform-aware)
    #[cfg(target_os = "windows")]
    let result = spawn_detached("cmd", &["/C", "start", "", url]);
    #[cfg(not(target_os = "windows"))]
    let result = spawn_detached("xdg-open", &[url]);

    if result.is_ok() {
        return Some(OpenAppResult {
            success: true,
            message: format!("{} abierto en navegador", app),
            method: "web".to_string(),
            app: app.to_string(),
        });
    }
    None
}

fn allowed_app(app: &str) -> bool {
    // Apps nativas permitidas (incluyendo navegadores)
    #[cfg(target_os = "windows")]
    let native_apps = [
        "discord", "spotify", "vlc", "steam", "code",
        "firefox", "chrome", "chromium", "edge", "brave", "browser", "navegador",
        "cmd", "powershell", "explorer", "calc", "taskmgr", "ms-settings",
    ];
    #[cfg(not(target_os = "windows"))]
    let native_apps = [
        "discord", "spotify", "vlc", "kate", "dolphin", "waitlos",
        "konsole", "steam", "code", "libreoffice", "systemsettings",
        "alacritty", "firefox", "chrome", "chromium", "browser", "navegador",
    ];
    if native_apps.contains(&app) {
        return true;
    }
    // Si es una web app (tiene URL), también permitir
    if web_app_url(app).is_some() {
        return true;
    }
    false
}

fn process_candidates(app: &str) -> Vec<&'static str> {
    match app {
        "firefox" => vec!["firefox"],
        #[cfg(target_os = "windows")]
        "chrome" => vec!["chrome"],
        #[cfg(not(target_os = "windows"))]
        "chrome" => vec!["google-chrome", "google-chrome-stable", "chrome"],
        "chromium" => vec!["chromium"],
        "discord" => vec!["Discord", "discord"],
        "spotify" => vec!["spotify"],
        "vlc" => vec!["vlc"],
        "steam" => vec!["steam", "steamwebhelper"],
        "code" => vec!["code"],
        #[cfg(not(target_os = "windows"))]
        "kate" => vec!["kate"],
        #[cfg(not(target_os = "windows"))]
        "dolphin" => vec!["dolphin"],
        #[cfg(not(target_os = "windows"))]
        "waitlos" => vec!["wasistlos", "WasIstLos", "waitlos"],
        #[cfg(not(target_os = "windows"))]
        "konsole" => vec!["konsole"],
        #[cfg(not(target_os = "windows"))]
        "libreoffice" => vec!["soffice", "soffice.bin", "oosplash"],
        #[cfg(not(target_os = "windows"))]
        "systemsettings" => vec!["systemsettings", "systemsettings5"],
        #[cfg(not(target_os = "windows"))]
        "alacritty" => vec!["alacritty"],
        _ => vec![],
    }
}

#[cfg(target_os = "linux")]
fn flatpak_ids(app: &str) -> Vec<&'static str> {
    match app {
        "discord" => vec!["com.discordapp.Discord"],
        "waitlos" => vec!["com.github.xeco23.WasIstLos"],
        "firefox" => vec!["org.mozilla.firefox"],
        "chromium" => vec!["org.chromium.Chromium"],
        "spotify" => vec!["com.spotify.Client"],
        "vlc" => vec!["org.videolan.VLC"],
        "steam" => vec!["com.valvesoftware.Steam"],
        "libreoffice" => vec!["org.libreoffice.LibreOffice"],
        _ => vec![],
    }
}

fn is_running(process_name: &str) -> bool {
    use sysinfo::System;
    let mut sys = System::new();
    sys.refresh_processes();
    let name_lower = process_name.to_lowercase();
    sys.processes().values().any(|p| {
        let pname = p.name().to_lowercase();
        pname == name_lower || pname.contains(&name_lower)
    })
}

fn is_any_running(names: &[&str]) -> bool {
    names.iter().any(|name| is_running(name))
}

fn wait_for_process(names: &[&str], timeout_secs: f32) -> bool {
    let start = Instant::now();
    let timeout = Duration::from_secs_f32(timeout_secs);
    while start.elapsed() < timeout {
        if is_any_running(names) {
            return true;
        }
        thread::sleep(Duration::from_millis(180));
    }
    false
}

fn find_in_path(bin: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(bin);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn spawn_detached(cmd: &str, args: &[&str]) -> Result<(), String> {
    let mut command = Command::new(cmd);
    command.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    // Windows: detach process from parent
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x00000008 | 0x00000200); // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    }

    // Linux: propagate graphical environment variables
    #[cfg(not(target_os = "windows"))]
    for (key, value) in std::env::vars() {
        command.env(key, value);
    }

    command.spawn()
        .map(|_| ())
        .map_err(|e| format!("{} {}", cmd, e))
}

fn launch_with_binary(app: &str) -> Option<OpenAppResult> {
    let candidates = process_candidates(app);
    let bin = match app {
        "firefox" => Some("firefox"),
        #[cfg(target_os = "windows")]
        "chrome" => Some("chrome"),
        #[cfg(not(target_os = "windows"))]
        "chrome" => Some("google-chrome-stable"),
        "chromium" => Some("chromium"),
        "discord" => {
            // Platform-specific Discord path
            #[cfg(target_os = "windows")]
            let discord_path = {
                let local_app = std::env::var("LOCALAPPDATA").unwrap_or_default();
                format!("{}\\Discord\\Update.exe", local_app)
            };
            #[cfg(not(target_os = "windows"))]
            let discord_path = "/opt/discord/Discord".to_string();

            if Path::new(&discord_path).exists() {
                #[cfg(target_os = "windows")]
                let launch_ok = spawn_detached(&discord_path, &["--processStart", "Discord.exe"]).is_ok();
                #[cfg(not(target_os = "windows"))]
                let launch_ok = spawn_detached(&discord_path, &["--no-sandbox", "--disable-gpu", "--disable-gpu-sandbox"]).is_ok();

                if launch_ok && wait_for_process(&candidates, 5.0) {
                    return Some(OpenAppResult {
                        success: true,
                        message: "Discord abierto (nativo, verificado)".to_string(),
                        method: "native".to_string(),
                        app: app.to_string(),
                    });
                }
            }
            Some("discord")
        }
        "spotify" => Some("spotify"),
        "vlc" => Some("vlc"),
        "steam" => Some("steam"),
        "code" => Some("code"),
        #[cfg(target_os = "windows")]
        "cmd" => Some("cmd"),
        #[cfg(target_os = "windows")]
        "powershell" => Some("powershell"),
        #[cfg(target_os = "windows")]
        "explorer" => Some("explorer"),
        #[cfg(target_os = "windows")]
        "calc" => Some("calc"),
        #[cfg(target_os = "windows")]
        "taskmgr" => Some("taskmgr"),
        #[cfg(target_os = "windows")]
        "edge" => Some("msedge"),
        #[cfg(target_os = "windows")]
        "brave" => Some("brave"),
        #[cfg(not(target_os = "windows"))]
        "kate" => Some("kate"),
        #[cfg(not(target_os = "windows"))]
        "dolphin" => Some("dolphin"),
        #[cfg(not(target_os = "windows"))]
        "waitlos" => Some("wasistlos"),
        #[cfg(not(target_os = "windows"))]
        "konsole" => Some("konsole"),
        #[cfg(not(target_os = "windows"))]
        "libreoffice" => Some("libreoffice"),
        #[cfg(not(target_os = "windows"))]
        "systemsettings" => Some("systemsettings"),
        #[cfg(not(target_os = "windows"))]
        "alacritty" => Some("alacritty"),
        _ => None,
    };

    let bin = bin?;
    if find_in_path(bin).is_none() {
        return None;
    }

    let launch_args: Vec<&str> = if app == "firefox" {
        let mut args = Vec::new();
        // Habilitar depuración remota para Playwright
        args.push("--remote-debugging-port");
        args.push("9222");
        args.push("--marionette");
        if is_any_running(&candidates) {
            args.push("--new-window");
        }
        args
    } else {
        vec![]
    };

    if spawn_detached(bin, &launch_args).is_ok() {
        if wait_for_process(&candidates, 2.5) || app == "waitlos" {
            return Some(OpenAppResult {
                success: true,
                message: format!("{} abierto (binario, verificado)", app),
                method: "binary".to_string(),
                app: app.to_string(),
            });
        }
        return Some(OpenAppResult {
            success: false,
            message: format!("{} no inició correctamente", app),
            method: "binary".to_string(),
            app: app.to_string(),
        });
    }

    None
}

#[cfg(target_os = "linux")]
fn launch_with_flatpak(app: &str) -> Option<OpenAppResult> {
    let candidates = process_candidates(app);
    for id in flatpak_ids(app) {
        let status = Command::new("flatpak")
            .arg("info")
            .arg(id)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .ok()?;

        if !status.success() {
            continue;
        }

        if spawn_detached("flatpak", &["run", id]).is_ok() {
            if wait_for_process(&candidates, 4.0) || app == "waitlos" {
                return Some(OpenAppResult {
                    success: true,
                    message: format!("{} abierto (flatpak, verificado)", app),
                    method: "flatpak".to_string(),
                    app: app.to_string(),
                });
            }

            return Some(OpenAppResult {
                success: false,
                message: format!("{} flatpak no inició correctamente", app),
                method: "flatpak".to_string(),
                app: app.to_string(),
            });
        }
    }
    None
}

#[cfg(target_os = "linux")]
fn candidate_desktop_ids(app: &str) -> Vec<&'static str> {
    match app {
        "discord" => vec!["discord", "com.discordapp.Discord"],
        "waitlos" => vec!["com.github.xeco23.wasistlos", "wasistlos", "waitlos"],
        "firefox" => vec!["firefox", "org.mozilla.firefox"],
        "chrome" => vec!["google-chrome", "google-chrome-stable"],
        "chromium" => vec!["chromium", "org.chromium.Chromium"],
        "spotify" => vec!["spotify", "com.spotify.Client"],
        "vlc" => vec!["vlc", "org.videolan.VLC"],
        "kate" => vec!["org.kde.kate", "kate"],
        "dolphin" => vec!["org.kde.dolphin", "dolphin"],
        "konsole" => vec!["org.kde.konsole", "konsole"],
        "steam" => vec!["steam", "com.valvesoftware.Steam"],
        "code" => vec!["code", "code-oss", "visual-studio-code"],
        "libreoffice" => vec!["libreoffice-startcenter", "org.libreoffice.LibreOffice"],
        "systemsettings" => vec!["systemsettings", "org.kde.systemsettings"],
        "alacritty" => vec!["alacritty", "org.alacritty.Alacritty"],
        _ => vec![],
    }
}

#[cfg(target_os = "linux")]
fn desktop_dirs() -> Vec<PathBuf> {
    let mut dirs = vec![
        PathBuf::from("/usr/share/applications"),
        PathBuf::from("/var/lib/flatpak/exports/share/applications"),
    ];

    if let Some(home) = std::env::var_os("HOME") {
        dirs.push(PathBuf::from(&home).join(".local/share/applications"));
        dirs.push(PathBuf::from(&home).join(".local/share/flatpak/exports/share/applications"));
    }

    dirs
}

#[cfg(target_os = "linux")]
fn find_desktop_id(app: &str) -> Option<String> {
    let needles = candidate_desktop_ids(app);
    for dir in desktop_dirs() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue, // directorio no existe, siguiente
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let file_name = match path.file_name() {
                Some(f) => f.to_string_lossy().to_lowercase(),
                None => continue,
            };
            if !file_name.ends_with(".desktop") {
                continue;
            }
            if needles.iter().any(|n| file_name.contains(n)) {
                return Some(file_name.trim_end_matches(".desktop").to_string());
            }
        }
    }
    None
}

#[cfg(target_os = "linux")]
fn launch_with_desktop(app: &str) -> Option<OpenAppResult> {
    let desktop_id = find_desktop_id(app)?;
    let candidates = process_candidates(app);

    if spawn_detached("gtk-launch", &[desktop_id.as_str()]).is_ok() {
        if wait_for_process(&candidates, 3.5) || app == "waitlos" {
            return Some(OpenAppResult {
                success: true,
                message: format!("{} abierto (desktop, verificado)", app),
                method: "desktop".to_string(),
                app: app.to_string(),
            });
        }

        return Some(OpenAppResult {
            success: false,
            message: format!("{} desktop no inició correctamente", app),
            method: "desktop".to_string(),
            app: app.to_string(),
        });
    }

    None
}

// Un comando Tauri SINCRÓNICO corre en el MAIN THREAD, el mismo que bombea los
// mensajes de la ventana. Estos dos bloquean segundos — `focus_app` espera a un
// `powershell` que compila C# con `Add-Type`, y `open_app` duerme hasta 5 s
// esperando que aparezca un proceso. Un thread de UI que deja de bombear, siendo
// dueño del tray icon y con la captura implícita del click del usuario todavía
// viva, es el único mecanismo de este repo capaz de producir "los clicks no hacen
// nada en ningún lado" y de soltarse al cerrar la ventana — que es exactamente lo
// que le pasó a Bruno el 2026-10-02.
//
// NO está probado que ésta haya sido la causa: ningún .js invoca estos comandos.
// Pero un arma cargada se descarga igual. `async fn` los saca del main thread.
#[tauri::command]
async fn open_app(app_name: String) -> OpenAppResult {
    let app = canonical_app_name(&app_name);

    if !allowed_app(&app) {
        return OpenAppResult {
            success: false,
            message: format!("Aplicación no permitida: {}", app),
            method: "guard".to_string(),
            app,
        };
    }

    // Web apps: open via default browser
    if let Some(url) = web_app_url(&app) {
        if let Some(result) = launch_with_web(&app, &url) {
            return result;
        }
    }

    if let Some(result) = launch_with_binary(&app) {
        if result.success {
            return result;
        }
    }

    #[cfg(target_os = "linux")]
    {
        if let Some(result) = launch_with_flatpak(&app) {
            if result.success {
                return result;
            }
        }

        if let Some(result) = launch_with_desktop(&app) {
            if result.success {
                return result;
            }
        }
    }

    OpenAppResult {
        success: false,
        message: format!("No pude abrir '{}' con métodos nativos (binary/flatpak/desktop)", app),
        method: "none".to_string(),
        app,
    }
}

/// Trae la ventana de una app desktop al frente si ya está corriendo.
/// Usado cuando el backend devuelve "ya está abierto" pero la ventana está
/// minimizada o en segundo plano.
#[tauri::command]
async fn focus_app(app_name: String) -> serde_json::Value {
    let app = canonical_app_name(&app_name);

    if !is_any_running(&process_candidates(&app)) {
        return serde_json::json!({
            "success": false,
            "message": format!("{} no está corriendo.", app),
        });
    }

    let focused = focus_window_for_app(&app);

    serde_json::json!({
        "success": focused,
        "message": if focused {
            format!("✓ Ventana de {} al frente.", app)
        } else {
            format!("No se pudo traer la ventana de {} al frente.", app)
        },
    })
}

fn focus_window_for_app(app: &str) -> bool {
    #[cfg(target_os = "windows")]
    return focus_window_windows(app);

    #[cfg(not(target_os = "windows"))]
    return focus_window_linux(app);
}

#[cfg(target_os = "windows")]
fn focus_window_windows(app: &str) -> bool {
    let title_pattern = match app {
        "discord" => Some("discord"),
        "spotify" => Some("spotify"),
        "vlc" => Some("vlc"),
        "code" => Some("visual studio code"),
        "steam" => Some("steam"),
        "firefox" => Some("firefox"),
        "kate" => Some("kate"),
        "konsole" => Some("konsole"),
        "libreoffice" => Some("libreoffice"),
        _ => None,
    };

    if let Some(pattern) = title_pattern {
        let ps_script = format!(
            r#"Add-Type -Name Win32Activate -Namespace Temp @"
using System;
using System.Runtime.InteropServices;
public class Win32Activate {{
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    private static string _pattern;
    private static bool _found;
    public static bool Activate(string pattern) {{
        _pattern = pattern.ToLower();
        _found = false;
        EnumWindows(OnEnumWindow, IntPtr.Zero);
        return _found;
    }}
    static bool OnEnumWindow(IntPtr hWnd, IntPtr lParam) {{
        if (!IsWindowVisible(hWnd)) return true;
        var sb = new System.Text.StringBuilder(512);
        GetWindowText(hWnd, sb, 512);
        var title = sb.ToString().ToLower();
        if (title.Contains(_pattern) && title.Length > 0) {{
            ShowWindow(hWnd, 9); // SW_RESTORE
            SetForegroundWindow(hWnd);
            _found = true;
            return false;
        }}
        return true;
    }}
}}
"@
[Temp.Win32Activate]::Activate("{}")
"#,
            pattern
        );
        if let Ok(output) = std::process::Command::new("powershell")
            .args(["-NoProfile", "-NoLogo", "-Command", &ps_script])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
        {
            if output.success() {
                return true;
            }
        }
    }

    false
}

#[cfg(not(target_os = "windows"))]
fn focus_window_linux(app: &str) -> bool {
    let class = match app {
        "discord" => "discord",
        "spotify" => "Spotify",
        "vlc" => "vlc",
        "code" => "Code",
        "steam" => "Steam",
        "firefox" => "firefox",
        "kate" => "kate",
        "konsole" => "konsole",
        "libreoffice" => "libreoffice",
        _ => return false,
    };

    // Try xdotool first
    if let Ok(output) = std::process::Command::new("xdotool")
        .args(["search", "--class", class, "windowactivate"])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
    {
        if output.success() {
            return true;
        }
    }

    // Fallback: wmctrl
    if let Ok(output) = std::process::Command::new("wmctrl")
        .args(["-xa", class])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
    {
        if output.success() {
            return true;
        }
    }

    false
}

/// Minimize the main window to the taskbar. Used by the CALL MIN button to
/// give the user the desktop back while keeping the call audio + WS alive.
#[tauri::command]
async fn minimize_main_window(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    let w = app
        .get_webview_window("main")
        .ok_or_else(|| "main window not found".to_string())?;
    w.minimize().map_err(|e| e.to_string())?;
    Ok(())
}

/// Call-mode background: spawns a floating always-on-top window that mirrors
/// call state, then hides (not closes) the main window. The AudioContext,
/// MediaStream and WebSocket all live on the main webview — hiding keeps them
/// alive, so the call never drops. `exit_background_mode` reverses it.
#[tauri::command]
async fn enter_background_mode(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::{WebviewUrl, WebviewWindowBuilder, Emitter};

    if app.get_webview_window("call-floating").is_none() {
        let _floating = WebviewWindowBuilder::new(
            &app,
            "call-floating",
            WebviewUrl::App("call-floating.html".into()),
        )
        .title("Zephyro · Call")
        .inner_size(320.0, 92.0)
        .min_inner_size(320.0, 92.0)
        .max_inner_size(360.0, 360.0)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(true)
        .transparent(true)
        .build()
        .map_err(|e| e.to_string())?;
    } else if let Some(w) = app.get_webview_window("call-floating") {
        let _ = w.set_focus();
    }

    // Give the floating window a moment to mount its event listener before hiding main.
    // Use spawn_blocking so the sleep happens off-runtime — std::thread::sleep would
    // freeze the entire Tokio executor and stall every other Tauri command for 150ms.
    let _ = tauri::async_runtime::spawn_blocking(|| {
        std::thread::sleep(std::time::Duration::from_millis(150));
    })
    .await;

    if let Some(main) = app.get_webview_window("main") {
        if let Err(e) = main.hide() {
            eprintln!("[enter_background_mode] main.hide failed: {}", e);
        }
    }

    // Nudge main to emit its current state snapshot.
    let _ = app.emit("call-request-snapshot", ());

    Ok("background".into())
}

#[tauri::command]
async fn exit_background_mode(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::Emitter;
    if let Some(floating) = app.get_webview_window("call-floating") {
        if let Err(e) = floating.close() {
            eprintln!("[exit_background_mode] floating.close failed: {}", e);
        }
    }
    if let Some(main) = app.get_webview_window("main") {
        if let Err(e) = main.show() {
            eprintln!("[exit_background_mode] main.show failed: {}", e);
        }
        if let Err(e) = main.set_focus() {
            eprintln!("[exit_background_mode] main.set_focus failed: {}", e);
        }
    } else {
        eprintln!("[exit_background_mode] main window not found — UI may be unrecoverable");
    }
    let _ = app.emit("call-restored", ());
    Ok("foreground".into())
}

/// Invoked by the floating window when the user hits its ✕. Restores the
/// main window and forwards an "end-call" event so chat.js can tear down.
#[tauri::command]
async fn end_call_from_floating(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::Emitter;
    if let Some(floating) = app.get_webview_window("call-floating") {
        if let Err(e) = floating.close() {
            eprintln!("[end_call_from_floating] floating.close failed: {}", e);
        }
    }
    if let Some(main) = app.get_webview_window("main") {
        if let Err(e) = main.show() {
            eprintln!("[end_call_from_floating] main.show failed: {}", e);
        }
        if let Err(e) = main.set_focus() {
            eprintln!("[end_call_from_floating] main.set_focus failed: {}", e);
        }
    } else {
        eprintln!("[end_call_from_floating] main window not found — UI may be unrecoverable");
    }
    let _ = app.emit("call-end-requested", ());
    Ok("end_requested".into())
}

/// Abre (o trae al frente) la ventana de Salud. Si la ventana no existe (por
/// ejemplo porque tauri.conf no la registró), la crea on-demand.
#[tauri::command]
async fn open_health_window(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::{WebviewUrl, WebviewWindowBuilder};
    if let Some(w) = app.get_webview_window("health") {
        w.show().map_err(|e| e.to_string())?;
        w.unminimize().ok();
        w.set_focus().map_err(|e| e.to_string())?;
        return Ok("shown".to_string());
    }
    let _w = WebviewWindowBuilder::new(&app, "health", WebviewUrl::App("health.html".into()))
        .title("Jarvis — Salud")
        .inner_size(900.0, 700.0)
        .min_inner_size(720.0, 560.0)
        .resizable(true)
        .decorations(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok("created".to_string())
}

/// Abre (o trae al frente) la ventana de Agenda. Mismo patrón que
/// `open_health_window`: si la ventana ya existe la muestra/enfoca; si no, la
/// crea on-demand cargando `agenda.html`.
#[tauri::command]
async fn open_agenda_window(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::{WebviewUrl, WebviewWindowBuilder};
    if let Some(w) = app.get_webview_window("agenda") {
        w.show().map_err(|e| e.to_string())?;
        w.unminimize().ok();
        w.set_focus().map_err(|e| e.to_string())?;
        return Ok("shown".to_string());
    }
    let _w = WebviewWindowBuilder::new(&app, "agenda", WebviewUrl::App("agenda.html".into()))
        .title("Zephyro — Agenda")
        .inner_size(960.0, 720.0)
        .min_inner_size(720.0, 540.0)
        .resizable(true)
        .decorations(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok("created".to_string())
}

/// Abre (o trae al frente) la ventana de gestión de APIs. Mismo patrón que
/// `open_agenda_window`: si ya existe la muestra/enfoca; si no, la crea
/// on-demand cargando `api-window.html`.
#[tauri::command]
async fn open_api_window(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::{WebviewUrl, WebviewWindowBuilder};
    if let Some(w) = app.get_webview_window("apis") {
        w.show().map_err(|e| e.to_string())?;
        w.unminimize().ok();
        w.set_focus().map_err(|e| e.to_string())?;
        return Ok("shown".to_string());
    }
    let _w = WebviewWindowBuilder::new(&app, "apis", WebviewUrl::App("api-window.html".into()))
        .title("Zephyro — APIs")
        .inner_size(960.0, 720.0)
        .min_inner_size(720.0, 540.0)
        .resizable(true)
        .decorations(true)
        .build()
        .map_err(|e| e.to_string())?;
    Ok("created".to_string())
}

/// Forwards a state snapshot from main → floating window.
#[tauri::command]
fn push_call_state(app: tauri::AppHandle, state: serde_json::Value) -> Result<(), String> {
    use tauri::{Emitter, Manager};
    // If the floating window has been closed (call ended, user dismissed it,
    // crash, etc.) silently drop the snapshot instead of trying to emit into
    // a dead channel, which can panic the IPC layer.
    if app.get_webview_window("call-floating").is_none() {
        return Ok(());
    }
    app.emit_to("call-floating", "call-state", state)
        .map_err(|e| e.to_string())
}

/// Resize the floating call window (used when the diagnostic strip expands).
#[tauri::command]
async fn set_floating_height(app: tauri::AppHandle, height: u32) -> Result<(), String> {
    use tauri::{LogicalSize, Size};
    if let Some(w) = app.get_webview_window("call-floating") {
        let current = w.inner_size().map_err(|e| e.to_string())?;
        let scale = w.scale_factor().unwrap_or(1.0);
        let logical_width = (current.width as f64) / scale;
        w.set_size(Size::Logical(LogicalSize::new(logical_width, height as f64)))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Forward a diagnostic-state snapshot from main → floating window.
#[tauri::command]
fn push_diag_state(app: tauri::AppHandle, state: serde_json::Value) -> Result<(), String> {
    use tauri::{Emitter, Manager};
    if app.get_webview_window("call-floating").is_none() {
        return Ok(());
    }
    app.emit_to("call-floating", "diag-state", state)
        .map_err(|e| e.to_string())
}

/// Called by the floating window when the user clicks an approval button.
/// Forwards the action back to main (which holds the WebSocket).
#[tauri::command]
fn diag_response(app: tauri::AppHandle, request_id: String, action: String) -> Result<(), String> {
    use tauri::Emitter;
    let payload = serde_json::json!({
        "request_id": request_id,
        "action": action,
    });
    app.emit_to("main", "diag-response", payload)
        .map_err(|e| e.to_string())
}

/// Opens the GitNexus Web UI in a new Tauri window.
/// Called from frontend via `/code explore` command.
#[tauri::command]
async fn open_gitnexus_ui(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::{WebviewUrl, WebviewWindowBuilder};

    // Check if window already exists
    if let Some(window) = app.get_webview_window("gitnexus") {
        window.set_focus().map_err(|e| e.to_string())?;
        return Ok("GitNexus window focused".to_string());
    }

    // Create a new window for GitNexus Web UI
    let gitnexus_url = "http://localhost:3000"
        .parse()
        .map_err(|e| format!("Invalid GitNexus URL: {}", e))?;
    let _window = WebviewWindowBuilder::new(
        &app,
        "gitnexus",
        WebviewUrl::External(gitnexus_url),
    )
    .title("GitNexus — Code Intelligence")
    .inner_size(1400.0, 900.0)
    .resizable(true)
    .build()
    .map_err(|e| e.to_string())?;

    Ok("GitNexus window opened".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

    let alt_space = Shortcut::new(Some(Modifiers::ALT), Code::Space);

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if event.state() == ShortcutState::Pressed && shortcut == &alt_space {
                        use tauri::{Emitter, Manager};

                        // If we're in call background mode (floating window exists),
                        // Alt+Space should fully restore — closing the floating window,
                        // showing main, and emitting `call-restored` so the JS state
                        // machine resyncs. Skipping this path leaves the call in a
                        // hybrid state (both windows visible, JS still flagged
                        // `background: true`) that only a restart can recover.
                        if let Some(floating) = app.get_webview_window("call-floating") {
                            if let Err(e) = floating.close() {
                                eprintln!("[alt-space] floating.close failed: {}", e);
                            }
                            if let Some(main) = app.get_webview_window("main") {
                                if let Err(e) = main.show() {
                                    eprintln!("[alt-space] main.show failed: {}", e);
                                }
                                if let Err(e) = main.set_focus() {
                                    eprintln!("[alt-space] main.set_focus failed: {}", e);
                                }
                            }
                            let _ = app.emit("call-restored", ());
                            return;
                        }

                        if let Some(window) = app.get_webview_window("main") {
                            // Si la ventana está oculta, mostrarla y enfocarla.
                            // Si está visible, emitir toggle de compact mode.
                            match window.is_visible() {
                                Ok(true) => {
                                    let _ = app.emit("compact-mode-toggle", ());
                                    let _ = window.set_focus();
                                }
                                _ => {
                                    let _ = window.show();
                                    let _ = window.set_focus();
                                }
                            }
                        }
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            greet,
            open_app,
            focus_app,
            open_gitnexus_ui,
            enter_background_mode,
            exit_background_mode,
            minimize_main_window,
            end_call_from_floating,
            push_call_state,
            set_floating_height,
            push_diag_state,
            diag_response,
            open_health_window,
            open_agenda_window,
            open_api_window,
        ])
        .setup(move |app| {
            // Registrar Alt+Space como global shortcut
            if let Err(e) = app.global_shortcut().register(alt_space) {
                eprintln!("[global-shortcut] No se pudo registrar Alt+Space: {}", e);
            }

            #[cfg(target_os = "linux")]
            {
                // Habilitar permisos de micrófono/cámara en WebKitGTK
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.with_webview(|webview| {
                        use webkit2gtk::WebViewExt;
                        use webkit2gtk::PermissionRequestExt;
                        let wv: webkit2gtk::WebView = webview.inner();
                        wv.connect_permission_request(|_wv: &webkit2gtk::WebView, req: &webkit2gtk::PermissionRequest| {
                            req.allow();
                            true
                        });
                    });
                }
            }

            // ── System Tray ──────────────────────────────────────────────
            let item_open_jarvis = MenuItem::with_id(app, "open_jarvis", "Abrir Jarvis", true, None::<&str>)?;
            let item_open_salud  = MenuItem::with_id(app, "open_salud",  "Abrir Salud",  true, None::<&str>)?;
            let item_sep         = PredefinedMenuItem::separator(app)?;
            let item_quit        = MenuItem::with_id(app, "quit", "Salir", true, None::<&str>)?;

            let menu = Menu::with_items(app, &[
                &item_open_jarvis,
                &item_open_salud,
                &item_sep,
                &item_quit,
            ])?;

            let _tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().cloned().unwrap())
                .menu(&menu)
                .tooltip("Jarvis — Asistente IA")
                .on_menu_event(|app, event| {
                    match event.id.as_ref() {
                        "open_jarvis" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                        "open_salud" => {
                            if let Some(w) = app.get_webview_window("health") {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                        "quit" => {
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    // Doble click → abre la ventana de Salud (Windows only)
                    if let TrayIconEvent::DoubleClick { button: MouseButton::Left, .. } = event {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("health") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
