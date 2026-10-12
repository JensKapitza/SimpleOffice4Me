use tauri::{Manager, Url};

#[cfg(not(mobile))]
struct ServerChooser(Url);

fn chooser_url(url: &Url) -> bool {
    let local_origin = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost"));
    local_origin
        && matches!(url.path(), "/" | "/index.html")
        && url.query().is_none()
        && url.fragment().is_none()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default().setup(|app| {
        let _window = tauri::WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
            .on_navigation(|url| {
                chooser_url(url)
                    || (url.scheme() == "https" && url.host_str() != Some("tauri.localhost"))
            })
            .build()?;
        #[cfg(not(mobile))]
        {
            app.manage(ServerChooser(_window.url()?));
            use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
            let configure = MenuItem::with_id(
                app,
                "configure-server",
                "Serveradresse ändern",
                true,
                Some("CmdOrCtrl+L"),
            )?;
            let quit = PredefinedMenuItem::quit(app, Some("Beenden"))?;
            let section = Submenu::with_items(app, "SimpleOffice4Me", true, &[&configure, &quit])?;
            app.set_menu(Menu::with_items(app, &[&section])?)?;
        }
        Ok(())
    });
    #[cfg(not(mobile))]
    let builder = builder.on_menu_event(|app, event| {
        if event.id().as_ref() == "configure-server" {
            if let Some(window) = app.get_webview_window("main") {
                if window
                    .navigate(app.state::<ServerChooser>().inner().0.clone())
                    .is_err()
                {
                    eprintln!("Serverauswahl konnte nicht geöffnet werden.");
                }
            }
        }
    });
    builder
        .run(tauri::generate_context!())
        .expect("failed to start SimpleOffice4Me Tauri");
}
