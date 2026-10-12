use armada_rgb::{
    ChannelBackend, ColorCorrection, Controller, LightingBackend, LightingConfig, MulticolorBackend,
};
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static NEXT: AtomicU64 = AtomicU64::new(0);

/// Packaged model name of the Lenovo Legion G9 (Tab Gen 5) controller dock.
const G9_MODEL: &str = "Lenovo Legion Tab Y700 5th Gen";
/// `HID_ID` line the G9's vendor interface reports (USB 3537:1136).
const G9_HID_ID: &str = "HID_ID=0003:00003537:00001136";
/// Same vendor, wrong product: the older 3537:1134 controller.
const G9_HID_ID_WRONG_PRODUCT: &str = "HID_ID=0003:00003537:00001134";
/// Wrong vendor id with the G9 product id.
const G9_HID_ID_WRONG_VENDOR: &str = "HID_ID=0003:0000ABCD:00001136";

/// Synthetic report descriptor fixture. Only its first three bytes -- the
/// vendor usage page (0xFF7A) that marks the interface answering GCM commands
/// -- are verified against the live device; the remaining bytes are filler.
const GCM_DESCRIPTOR: [u8; 10] = [0x06, 0x7A, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x06, 0x95];
/// A non-vendor generic-desktop gamepad interface.
const GAMEPAD_DESCRIPTOR: [u8; 6] = [0x05, 0x01, 0x09, 0x05, 0xA1, 0x01];
/// A non-vendor consumer-control interface.
const CONSUMER_DESCRIPTOR: [u8; 5] = [0x05, 0x0C, 0x09, 0x01, 0xA1];

struct Fixture {
    root: PathBuf,
    backlight: PathBuf,
    config: PathBuf,
    dev: PathBuf,
    hidraw: PathBuf,
    leds: PathBuf,
    model: PathBuf,
    profiles: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let nonce: u128 = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root: PathBuf = std::env::temp_dir().join(format!(
            "rgb-test-{}-{nonce}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let leds: PathBuf = root.join("leds");
        let backlight: PathBuf = root.join("backlight");
        fs::create_dir_all(&leds).unwrap();
        fs::create_dir_all(backlight.join("panel")).unwrap();
        fs::write(backlight.join("panel/brightness"), "50").unwrap();
        fs::write(backlight.join("panel/max_brightness"), "100").unwrap();

        let fixture = Self {
            config: root.join("etc/rgb.json"),
            backlight,
            dev: root.join("dev"),
            hidraw: root.join("hidraw"),
            leds,
            model: root.join("model"),
            profiles: root.join("profiles.json"),
            root,
        };
        fs::create_dir_all(&fixture.hidraw).unwrap();
        fs::write(&fixture.model, b"Test Device\0").unwrap();
        fixture
    }

    fn controller(&self, targets: &[String]) -> Controller {
        let backend: MulticolorBackend =
            MulticolorBackend::new(self.leds.clone(), targets.to_vec());
        Controller::new(self.config.clone(), LightingBackend::Multicolor(backend))
    }

    fn target(&self, name: &str, order: &str, maximum: &str) {
        let path: PathBuf = self.leds.join(name);
        fs::create_dir_all(&path).unwrap();
        fs::write(path.join("multi_index"), order).unwrap();
        fs::write(path.join("max_brightness"), maximum).unwrap();
        fs::write(path.join("multi_intensity"), "unchanged\n").unwrap();
        fs::write(path.join("brightness"), "unchanged\n").unwrap();
    }

    fn channel_target(&self, name: &str, maximum: &str) {
        let path: PathBuf = self.leds.join(name);
        fs::create_dir_all(&path).unwrap();
        fs::write(path.join("max_brightness"), maximum).unwrap();
        fs::write(path.join("brightness"), "unchanged\n").unwrap();
    }

    fn serial_target(&self, name: &str) -> PathBuf {
        fs::create_dir_all(&self.dev).unwrap();
        let path: PathBuf = self.dev.join(name);
        fs::write(&path, b"").unwrap();
        path
    }

    fn value(&self, target: &str, attribute: &str) -> String {
        fs::read_to_string(self.leds.join(target).join(attribute))
            .unwrap()
            .lines()
            .next()
            .unwrap()
            .into()
    }

    fn command(&self, backend: &str, targets: &[&str], correction: Option<&str>) -> Command {
        let correction: Option<ColorCorrection> = correction.map(|value| value.parse().unwrap());
        let catalog = serde_json::json!({
            "version": 1,
            "profiles": [{
                "models": ["Test Device"],
                "backend": {
                    "type": backend,
                    "targets": targets,
                },
                "correction": correction,
            }],
        });
        fs::write(&self.profiles, serde_json::to_vec(&catalog).unwrap()).unwrap();
        self.binary()
    }

    fn serial_command(&self) -> Command {
        fs::write(&self.model, b"AYANEO Pocket MICRO 2\0").unwrap();
        fs::write(&self.profiles, include_bytes!("../profiles.json")).unwrap();
        self.binary()
    }

    /// Fake sysfs `hidraw` node under `fixture.root/hidraw`: a `device/uevent`
    /// carrying `hid_id` and a `device/report_descriptor` with `descriptor`.
    fn hidraw_sysfs(&self, name: &str, hid_id: &str, descriptor: &[u8]) {
        let device: PathBuf = self.hidraw.join(name).join("device");
        fs::create_dir_all(&device).unwrap();
        fs::write(
            device.join("uevent"),
            format!("DRIVER=hid-generic\n{hid_id}\nHID_NAME=GameSir GCM\n"),
        )
        .unwrap();
        fs::write(device.join("report_descriptor"), descriptor).unwrap();
    }

    /// Regular file standing in for `/dev/hidrawN`; the backend writes reports
    /// into it just like a character device.
    fn hidraw_node(&self, name: &str) -> PathBuf {
        fs::create_dir_all(&self.dev).unwrap();
        let path: PathBuf = self.dev.join(name);
        fs::write(&path, b"").unwrap();
        path
    }

    /// A complete fake controller: sysfs node plus its writable device file.
    fn hidraw(&self, name: &str, hid_id: &str, descriptor: &[u8]) -> PathBuf {
        self.hidraw_sysfs(name, hid_id, descriptor);
        self.hidraw_node(name)
    }

    /// A catalog with a single `gcmhid` profile for the G9 (vendor 3537,
    /// product 1136, strips 1 and 2) and the G9 as the connected model.
    fn gcmhid_command(&self, model: &str) -> Command {
        let catalog = serde_json::json!({
            "version": 1,
            "profiles": [{
                "models": [model],
                "backend": {
                    "type": "gcmhid",
                    "vendor": "3537",
                    "product": "1136",
                    "strips": [1, 2],
                },
            }],
        });
        fs::write(&self.profiles, serde_json::to_vec(&catalog).unwrap()).unwrap();
        fs::write(&self.model, format!("{model}\0")).unwrap();
        self.binary()
    }

    /// The shipped catalog, with `model` as the connected device.
    fn packaged_command(&self, model: &str) -> Command {
        fs::write(&self.model, format!("{model}\0")).unwrap();
        fs::write(&self.profiles, include_bytes!("../profiles.json")).unwrap();
        self.binary()
    }

    fn binary(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_armada-rgb"));
        command
            .env("ARMADA_RGB_CONFIG_PATH", &self.config)
            .env("ARMADA_RGB_SYSFS_ROOT", &self.leds)
            .env("ARMADA_BACKLIGHT_ROOT", &self.backlight)
            .env("ARMADA_RGB_DEV_ROOT", &self.dev)
            .env("ARMADA_RGB_HIDRAW_ROOT", &self.hidraw)
            .env("ARMADA_RGB_MODEL_PATH", &self.model)
            .env("ARMADA_RGB_PROFILES_PATH", &self.profiles);
        command
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn enabled(color: &str, brightness: u8) -> LightingConfig {
    LightingConfig {
        version: 1,
        enabled: true,
        link_brightness: false,
        brightness,
        max_brightness: 25,
        color: color.into(),
        saturation: 100,
        correction: None,
    }
}

#[test]
fn defaults_and_config_round_trip() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");
    let controller: Controller = fixture.controller(&["rgb:l1".into()]);

    let defaults: LightingConfig = controller.get().unwrap();
    assert!(!defaults.enabled);
    assert_eq!(defaults.brightness, 25);

    let saved: LightingConfig = controller.set(enabled("abcdef", 40)).unwrap();
    assert_eq!(saved.color, "ABCDEF");
    assert_eq!(controller.get().unwrap(), saved);
    assert!(!fixture.config.with_extension("tmp").exists());
}

#[test]
fn thor_bgr_targets_do_not_touch_other_leds() {
    let fixture: Fixture = Fixture::new();
    let targets: Vec<String> = ["l1", "l2", "l3", "l4", "r1", "r2", "r3", "r4"]
        .map(|name| format!("rgb:{name}"))
        .into();
    for target in &targets {
        fixture.target(target, "blue green red", "255");
    }
    fixture.target("power-led", "red green blue", "511");
    let controller: Controller = fixture.controller(&targets);

    controller.set(enabled("FF8000", 25)).unwrap();

    for target in targets {
        assert_eq!(fixture.value(&target, "multi_intensity"), "0 55 255");
        assert_eq!(fixture.value(&target, "brightness"), "64");
    }
    assert_eq!(fixture.value("power-led", "brightness"), "unchanged");
}

#[test]
fn saturation_is_applied_to_multicolor_output() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");

    let output: std::process::Output = fixture
        .command("multicolor", &["rgb:l1"], None)
        .args([
            "set",
            "--color",
            "FF0000",
            "--saturation",
            "50",
            "--brightness",
            "100",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(fixture.value("rgb:l1", "multi_intensity"), "55 55 255");
}

#[test]
fn linked_brightness_scales_output_without_changing_saved_config() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");

    let output: std::process::Output = fixture
        .command("multicolor", &["rgb:l1"], None)
        .args([
            "set",
            "--enabled",
            "true",
            "--link-brightness",
            "true",
            "--max-brightness",
            "50",
            "--color",
            "FF0000",
            "--brightness",
            "80",
        ])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let saved: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert!(saved.enabled);
    assert!(saved.link_brightness);
    assert_eq!(saved.max_brightness, 50);
    assert_eq!(saved.brightness, 80);
    assert_eq!(fixture.value("rgb:l1", "multi_intensity"), "0 0 255");
    assert_eq!(fixture.value("rgb:l1", "brightness"), "64");

    let output: std::process::Output = fixture
        .command("multicolor", &["rgb:l1"], None)
        .arg("get")
        .output()
        .unwrap();
    let persisted: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(persisted, saved);
}

#[test]
fn odin3_channel_targets_do_not_touch_other_leds() {
    let fixture: Fixture = Fixture::new();
    let targets: Vec<String> = [
        "red=l:r1",
        "red=r:r1",
        "green=l:g1",
        "green=r:g1",
        "blue=l:b1",
        "blue=r:b1",
    ]
    .map(str::to_owned)
    .into();
    for target in ["l:r1", "r:r1", "l:g1", "r:g1", "l:b1", "r:b1"] {
        fixture.channel_target(target, "255");
    }
    fixture.channel_target("power-led", "255");
    let backend: ChannelBackend = ChannelBackend::new(fixture.leds.clone(), targets);
    let controller: Controller =
        Controller::new(fixture.config.clone(), LightingBackend::Channels(backend));

    controller.set(enabled("FF8000", 25)).unwrap();

    for target in ["l:r1", "r:r1"] {
        assert_eq!(fixture.value(target, "brightness"), "64");
    }
    for target in ["l:g1", "r:g1"] {
        assert_eq!(fixture.value(target, "brightness"), "14");
    }
    for target in ["l:b1", "r:b1"] {
        assert_eq!(fixture.value(target, "brightness"), "0");
    }
    assert_eq!(fixture.value("power-led", "brightness"), "unchanged");

    controller.off().unwrap();
    for target in ["l:r1", "r:r1", "l:g1", "r:g1", "l:b1", "r:b1"] {
        assert_eq!(fixture.value(target, "brightness"), "0");
    }
}

#[test]
fn cli_supports_channel_backend() {
    let fixture: Fixture = Fixture::new();
    for target in ["l:r1", "l:g1", "l:b1"] {
        fixture.channel_target(target, "255");
    }
    let output: std::process::Output = fixture
        .command(
            "channels",
            &["red=l:r1", "green=l:g1", "blue=l:b1"],
            Some("always:0,20,20"),
        )
        .args(["set", "--color", "00ffff", "--brightness", "100"])
        .output()
        .unwrap();

    assert!(output.status.success());
    assert_eq!(fixture.value("l:r1", "brightness"), "0");
    assert_eq!(fixture.value("l:g1", "brightness"), "154");
    assert_eq!(fixture.value("l:b1", "brightness"), "154");
}

#[test]
fn air_y_pro_profile_controls_both_aw20036_rings() {
    let fixture: Fixture = Fixture::new();
    fs::write(&fixture.model, b"MANGMI Air Y Pro\0").unwrap();
    fs::write(&fixture.profiles, include_bytes!("../profiles.json")).unwrap();

    for index in 0..16 {
        for color in ["red", "green", "blue"] {
            fixture.channel_target(&format!("{color}:indicator-{index}"), "255");
        }
    }

    let output: std::process::Output = Command::new(env!("CARGO_BIN_EXE_armada-rgb"))
        .env("ARMADA_RGB_CONFIG_PATH", &fixture.config)
        .env("ARMADA_RGB_SYSFS_ROOT", &fixture.leds)
        .env("ARMADA_RGB_MODEL_PATH", &fixture.model)
        .env("ARMADA_RGB_PROFILES_PATH", &fixture.profiles)
        .args(["set", "--color", "FF8000", "--brightness", "25"])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    for index in 0..16 {
        assert_eq!(
            fixture.value(&format!("red:indicator-{index}"), "brightness"),
            "64"
        );
        assert_eq!(
            fixture.value(&format!("green:indicator-{index}"), "brightness"),
            "14"
        );
        assert_eq!(
            fixture.value(&format!("blue:indicator-{index}"), "brightness"),
            "0"
        );
    }
}

#[test]
fn pocket_micro2_profile_sends_static_frames_to_the_mcu() {
    let fixture: Fixture = Fixture::new();
    let tty: PathBuf = fixture.serial_target("ttyHS2");
    let run = |args: &[&str]| fixture.serial_command().args(args).output().unwrap();

    let output: std::process::Output = run(&["set", "--color", "FF8000", "--brightness", "25"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let frame: [u8; 11] = [0xF7, 0x01, 0x40, 0x20, 0x00, 0, 0, 0, 0, 0x61, 0xED];
    assert_eq!(fs::read(&tty).unwrap(), frame.repeat(3));

    let output: std::process::Output = run(&["off"]);
    assert!(output.status.success());
    let frame: [u8; 11] = [0xF7, 0x01, 0, 0, 0, 0, 0, 0, 0, 0x01, 0xED];
    assert_eq!(fs::read(&tty).unwrap(), frame.repeat(3));
}

#[test]
fn sleep_blanks_the_mcu_without_saving() {
    let fixture: Fixture = Fixture::new();
    let tty: PathBuf = fixture.serial_target("ttyHS2");
    let run = |args: &[&str]| fixture.serial_command().args(args).output().unwrap();

    let output: std::process::Output = run(&["set", "--color", "FF8000", "--brightness", "25"]);
    assert!(output.status.success());
    let saved: Vec<u8> = fs::read(&fixture.config).unwrap();

    let output: std::process::Output = run(&["sleep"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let frame: [u8; 11] = [0xF7, 0x01, 0, 0, 0, 0, 0, 0, 0, 0x01, 0xED];
    assert_eq!(fs::read(&tty).unwrap(), frame.repeat(3));
    assert_eq!(fs::read(&fixture.config).unwrap(), saved);
}

#[test]
fn sleep_leaves_kernel_led_backends_alone() {
    let fixture: Fixture = Fixture::new();
    for target in ["l:r1", "l:g1", "l:b1"] {
        fixture.channel_target(target, "255");
    }
    let backend: ChannelBackend = ChannelBackend::new(
        fixture.leds.clone(),
        vec!["red=l:r1".into(), "green=l:g1".into(), "blue=l:b1".into()],
    );
    let controller: Controller =
        Controller::new(fixture.config.clone(), LightingBackend::Channels(backend));

    controller.sleep().unwrap();
    for target in ["l:r1", "l:g1", "l:b1"] {
        assert_eq!(fixture.value(target, "brightness"), "unchanged");
    }
    assert!(!fixture.config.exists());

    fixture.target("rgb:l1", "blue green red", "255");
    fixture.controller(&["rgb:l1".into()]).sleep().unwrap();
    assert_eq!(fixture.value("rgb:l1", "brightness"), "unchanged");
    assert_eq!(fixture.value("rgb:l1", "multi_intensity"), "unchanged");
}

#[test]
fn wake_leaves_kernel_led_backends_alone() {
    let fixture: Fixture = Fixture::new();
    let targets: [&str; 3] = ["red=l:r1", "green=l:g1", "blue=l:b1"];
    for target in ["l:r1", "l:g1", "l:b1"] {
        fixture.channel_target(target, "255");
    }

    let output: std::process::Output = fixture
        .command("channels", &targets, None)
        .arg("wake")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    for target in ["l:r1", "l:g1", "l:b1"] {
        assert_eq!(fixture.value(target, "brightness"), "unchanged");
        assert_eq!(fixture.value(target, "max_brightness"), "255");
    }
    assert!(!fixture.config.exists());
}

#[test]
fn wake_resends_the_saved_color_to_the_mcu() {
    let fixture: Fixture = Fixture::new();
    let tty: PathBuf = fixture.serial_target("ttyHS2");
    let run = |args: &[&str]| fixture.serial_command().args(args).output().unwrap();

    let output: std::process::Output = run(&["set", "--color", "FF8000", "--brightness", "25"]);
    assert!(output.status.success());
    let saved: Vec<u8> = fs::read(&fixture.config).unwrap();
    let output: std::process::Output = run(&["sleep"]);
    assert!(output.status.success());

    fs::write(&tty, b"").unwrap();
    let output: std::process::Output = run(&["wake"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let frame: [u8; 11] = [0xF7, 0x01, 0x40, 0x20, 0x00, 0, 0, 0, 0, 0x61, 0xED];
    assert_eq!(fs::read(&tty).unwrap(), frame.repeat(3));
    assert_eq!(fs::read(&fixture.config).unwrap(), saved);
}

#[test]
fn correction_preserves_the_user_color() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:sticks", "red green blue", "255");
    let run = |color: &str, correction: Option<&str>| {
        let mut command: Command =
            fixture.command("multicolor", &["rgb:sticks"], Some("red:0,20,20"));
        command.args(["set", "--color", color, "--brightness", "100"]);
        if let Some(correction) = correction {
            command.args(["--correction", correction]);
        }
        command.output().unwrap()
    };

    let output: std::process::Output = run("FFFF00", None);
    let config: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert!(output.status.success());
    assert_eq!(config.color, "FFFF00");
    assert_eq!(config.correction.unwrap().green, 20);
    assert_eq!(fixture.value("rgb:sticks", "multi_intensity"), "255 154 0");

    let output: std::process::Output = run("00FFFF", Some("red:10,30,40"));
    assert!(output.status.success());
    assert_eq!(fixture.value("rgb:sticks", "multi_intensity"), "0 255 255");

    let output: std::process::Output = run("FFFFFF", None);
    let config: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert!(output.status.success());
    assert_eq!(config.correction.unwrap().green, 30);
}

#[test]
fn target_errors_happen_before_writes() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");
    let targets: Vec<String> = vec!["rgb:l1".into(), "rgb:missing".into()];
    let controller: Controller = fixture.controller(&targets);

    assert!(controller.set(enabled("FFFFFF", 25)).is_err());
    assert_eq!(fixture.value("rgb:l1", "brightness"), "unchanged");
    assert!(!fixture.config.exists());
}

#[test]
fn preflight_rejects_an_unwritable_target() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");
    fixture.target("rgb:r1", "blue green red", "255");
    fs::set_permissions(
        fixture.leds.join("rgb:r1/multi_intensity"),
        fs::Permissions::from_mode(0o444),
    )
    .unwrap();
    let targets: Vec<String> = vec!["rgb:l1".into(), "rgb:r1".into()];
    let controller: Controller = fixture.controller(&targets);

    assert!(controller.set(enabled("FF0000", 10)).is_err());
    assert_eq!(fixture.value("rgb:l1", "brightness"), "unchanged");
    assert!(!fixture.config.exists());
}

#[test]
fn partial_writes_leave_targets_off() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");
    fixture.target("rgb:r1", "blue green red", "255");
    let brightness: PathBuf = fixture.leds.join("rgb:r1/brightness");
    fs::remove_file(&brightness).unwrap();
    symlink("/dev/full", brightness).unwrap();
    let targets: Vec<String> = vec!["rgb:l1".into(), "rgb:r1".into()];
    let controller: Controller = fixture.controller(&targets);

    assert!(controller.set(enabled("FF0000", 10)).is_err());
    assert_eq!(fixture.value("rgb:l1", "brightness"), "0");
    assert!(!fixture.config.exists());
}

#[test]
fn off_is_persistent_and_only_needs_brightness() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "100");
    let controller: Controller = fixture.controller(&["rgb:l1".into()]);
    controller.set(enabled("00FF00", 30)).unwrap();

    fs::write(fixture.leds.join("rgb:l1/multi_index"), "not rgb").unwrap();
    fs::write(fixture.leds.join("rgb:l1/max_brightness"), "zero").unwrap();
    let config: LightingConfig = controller.off().unwrap();

    assert!(!config.enabled);
    assert!(!controller.get().unwrap().enabled);
    assert_eq!(fixture.value("rgb:l1", "brightness"), "0");
}

#[test]
fn unsupported_devices_are_ignored_during_boot() {
    let fixture: Fixture = Fixture::new();
    fixture.target("power-led", "red green blue", "511");
    let controller: Controller = Controller::new(
        fixture.config.clone(),
        LightingBackend::Unsupported("not configured".into()),
    );

    assert_eq!(controller.apply().unwrap(), Some("not configured".into()));
    assert!(controller.set(enabled("FFFFFF", 25)).is_err());
    assert_eq!(fixture.value("power-led", "brightness"), "unchanged");
}

#[test]
fn cli_sets_gets_and_turns_off() {
    let fixture: Fixture = Fixture::new();
    fixture.target("rgb:l1", "blue green red", "255");
    let run = |args: &[&str]| {
        fixture
            .command("multicolor", &["rgb:l1"], None)
            .args(args)
            .output()
            .unwrap()
    };

    let output: std::process::Output = run(&["set", "--color", "aa00ff", "--brightness", "7"]);
    assert!(output.status.success());
    let config: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(config.color, "AA00FF");
    assert!(config.enabled);

    let output: std::process::Output = run(&["off"]);
    assert!(output.status.success());
    let config: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert!(!config.enabled);

    let output: std::process::Output = run(&["get"]);
    let config: LightingConfig = serde_json::from_slice(&output.stdout).unwrap();
    assert!(!config.enabled);

    let output: std::process::Output = run(&["set", "--color", "FFFFFF", "--brightness", "101"]);
    assert!(!output.status.success());
}

#[test]
fn cli_reports_profile_support() {
    let fixture: Fixture = Fixture::new();
    let output: std::process::Output = fixture
        .command("multicolor", &["rgb:l1"], None)
        .arg("supported")
        .output()
        .unwrap();
    assert!(output.status.success());

    fs::write(&fixture.model, b"Unknown Device\0").unwrap();
    let output: std::process::Output = fixture
        .command("multicolor", &["rgb:l1"], None)
        .arg("supported")
        .output()
        .unwrap();
    assert!(!output.status.success());
}

// --- G9 2026 GcmHid regression coverage -------------------------------------
//
// These tests drive the CLI only; they never link `GcmHidBackend`, so they
// still compile (and fail on their runtime assertions) before the production
// backend exists. They talk to a fake sysfs HID tree under `fixture.hidraw`
// and regular files under `fixture.dev`, selected via ARMADA_RGB_HIDRAW_ROOT.

#[test]
fn gcmhid_supported_tracks_the_connected_vendor_hid() {
    let fixture: Fixture = Fixture::new();
    let device: PathBuf = fixture.hidraw("hidraw3", G9_HID_ID, &GCM_DESCRIPTOR);

    let output: std::process::Output = fixture
        .gcmhid_command(G9_MODEL)
        .arg("supported")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "supported should accept the connected G9: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    // supported is read-only: it must not write to the controller.
    assert!(
        fs::read(&device).unwrap().is_empty(),
        "supported must not write to the device"
    );

    fs::remove_dir_all(fixture.hidraw.join("hidraw3")).unwrap();
    fs::remove_file(&device).unwrap();
    let output: std::process::Output = fixture
        .gcmhid_command(G9_MODEL)
        .arg("supported")
        .output()
        .unwrap();
    assert!(
        !output.status.success(),
        "supported should fail once the matching controller is gone"
    );
}

#[test]
fn gcmhid_supported_rejects_wrong_ids_and_nonvendor_descriptors() {
    let cases: [(&str, &str, &[u8]); 4] = [
        // Right descriptor, wrong product id (the older 3537:1134 controller).
        ("hidraw0", G9_HID_ID_WRONG_PRODUCT, GCM_DESCRIPTOR.as_slice()),
        // Right descriptor, wrong vendor id.
        ("hidraw1", G9_HID_ID_WRONG_VENDOR, GCM_DESCRIPTOR.as_slice()),
        // Right ids, but a non-vendor gamepad interface.
        ("hidraw2", G9_HID_ID, GAMEPAD_DESCRIPTOR.as_slice()),
        // Right ids, but a non-vendor consumer-control interface.
        ("hidraw3", G9_HID_ID, CONSUMER_DESCRIPTOR.as_slice()),
    ];
    for (name, hid_id, descriptor) in cases {
        let fixture: Fixture = Fixture::new();
        fixture.hidraw(name, hid_id, descriptor);
        let output: std::process::Output = fixture
            .gcmhid_command(G9_MODEL)
            .arg("supported")
            .output()
            .unwrap();
        assert!(
            !output.status.success(),
            "{name} ({hid_id}) must not be accepted as the controller"
        );

        // Adding the real controller on a later-sorted node must make support
        // true, proving selection rejected the impostor and not the connection.
        fixture.hidraw("hidraw9", G9_HID_ID, &GCM_DESCRIPTOR);
        let output: std::process::Output = fixture
            .gcmhid_command(G9_MODEL)
            .arg("supported")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "the valid node after {name} must be found: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn gcmhid_supported_requires_the_device_node() {
    let fixture: Fixture = Fixture::new();
    let device: PathBuf = fixture.hidraw("hidraw2", G9_HID_ID, &GCM_DESCRIPTOR);

    let output: std::process::Output = fixture
        .gcmhid_command(G9_MODEL)
        .arg("supported")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );

    fs::remove_file(&device).unwrap();
    let output: std::process::Output = fixture
        .gcmhid_command(G9_MODEL)
        .arg("supported")
        .output()
        .unwrap();
    assert!(
        !output.status.success(),
        "supported must be false without the writable device node"
    );
}

#[test]
fn gcmhid_writes_only_to_the_matching_vendor_hid() {
    let fixture: Fixture = Fixture::new();
    // Sorted first: same G9 ids but a non-vendor gamepad descriptor.
    fixture.hidraw_sysfs("hidraw0", G9_HID_ID, &GAMEPAD_DESCRIPTOR);
    let decoy: PathBuf = fixture.hidraw_node("hidraw0");
    // Wrong product id, right descriptor.
    fixture.hidraw_sysfs("hidraw1", G9_HID_ID_WRONG_PRODUCT, &GCM_DESCRIPTOR);
    let wrong_product: PathBuf = fixture.hidraw_node("hidraw1");
    // Wrong vendor id, right descriptor.
    fixture.hidraw_sysfs("hidraw2", G9_HID_ID_WRONG_VENDOR, &GCM_DESCRIPTOR);
    let wrong_vendor: PathBuf = fixture.hidraw_node("hidraw2");
    // Right ids, consumer-control descriptor.
    fixture.hidraw_sysfs("hidraw3", G9_HID_ID, &CONSUMER_DESCRIPTOR);
    let consumer: PathBuf = fixture.hidraw_node("hidraw3");
    // The real controller: right ids and the vendor descriptor.
    let controller: PathBuf = fixture.hidraw("hidraw7", G9_HID_ID, &GCM_DESCRIPTOR);

    let output: std::process::Output = fixture
        .gcmhid_command(G9_MODEL)
        .args([
            "set",
            "--color",
            "FF0000",
            "--brightness",
            "50",
            "--link-brightness",
            "false",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );

    let left: [u8; 13] = [0, 5, 12, 12, 1, 1, 0, 100, 50, 1, 128, 255, 53];
    let right: [u8; 13] = [0, 5, 12, 12, 1, 2, 0, 100, 50, 1, 128, 255, 54];
    let mut expected: Vec<u8> = Vec::new();
    expected.extend_from_slice(&left);
    expected.extend_from_slice(&right);
    assert_eq!(
        fs::read(&controller).unwrap(),
        expected,
        "only the matching node receives both strip reports"
    );
    for path in [&decoy, &wrong_product, &wrong_vendor, &consumer] {
        assert!(
            fs::read(path).unwrap().is_empty(),
            "unmatched device files must stay untouched: {}",
            path.display()
        );
    }
}

#[test]
fn gcmhid_set_writes_one_report_per_strip_and_persists() {
    let fixture: Fixture = Fixture::new();
    let device: PathBuf = fixture.hidraw("hidraw4", G9_HID_ID, &GCM_DESCRIPTOR);
    // An unrelated node must not receive the reports.
    let other: PathBuf = fixture.hidraw("hidraw1", G9_HID_ID_WRONG_VENDOR, &GCM_DESCRIPTOR);

    let output: std::process::Output = fixture
        .gcmhid_command(G9_MODEL)
        .args([
            "set",
            "--color",
            "FF0000",
            "--brightness",
            "50",
            "--link-brightness",
            "false",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );

    let trace: Vec<u8> = fs::read(&device).unwrap();
    let left: [u8; 13] = [0, 5, 12, 12, 1, 1, 0, 100, 50, 1, 128, 255, 53];
    let right: [u8; 13] = [0, 5, 12, 12, 1, 2, 0, 100, 50, 1, 128, 255, 54];
    let mut expected: Vec<u8> = Vec::new();
    expected.extend_from_slice(&left);
    expected.extend_from_slice(&right);
    assert_eq!(trace, expected, "RED at 50% is one 13-byte report per strip");
    assert!(
        fs::read(&other).unwrap().is_empty(),
        "unrelated node must stay untouched"
    );

    let saved: LightingConfig =
        serde_json::from_slice(&fs::read(&fixture.config).unwrap()).unwrap();
    assert!(saved.enabled);
    assert!(!saved.link_brightness);
    assert_eq!(saved.color, "FF0000");
    assert_eq!(saved.brightness, 50);
}

#[test]
fn gcmhid_off_writes_zero_hsb_with_effect_off() {
    let fixture: Fixture = Fixture::new();
    let device: PathBuf = fixture.hidraw("hidraw5", G9_HID_ID, &GCM_DESCRIPTOR);
    let run = |args: &[&str]| {
        fixture
            .gcmhid_command(G9_MODEL)
            .args(args)
            .output()
            .unwrap()
    };

    let output: std::process::Output = run(&[
        "set",
        "--color",
        "FF0000",
        "--brightness",
        "50",
        "--link-brightness",
        "false",
    ]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );

    // Regular files keep their bytes instead of streaming like a HID node, so
    // clear it before asserting the exact off trace.
    fs::write(&device, b"").unwrap();
    let output: std::process::Output = run(&["off"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );

    let trace: Vec<u8> = fs::read(&device).unwrap();
    let left: [u8; 13] = [0, 5, 12, 12, 1, 1, 0, 0, 0, 255, 128, 255, 157];
    let right: [u8; 13] = [0, 5, 12, 12, 1, 2, 0, 0, 0, 255, 128, 255, 158];
    let mut expected: Vec<u8> = Vec::new();
    expected.extend_from_slice(&left);
    expected.extend_from_slice(&right);
    assert_eq!(trace, expected, "off is zero HSB with effect 0xFF per strip");

    let saved: LightingConfig =
        serde_json::from_slice(&fs::read(&fixture.config).unwrap()).unwrap();
    assert!(!saved.enabled, "off must persist the disabled state");
}

#[test]
fn packaged_g9_profile_supports_every_alias() {
    let aliases: [&str; 3] = [
        "Lenovo Legion Tab Y700 5th Gen",
        "Lenovo Legion Y700 Gen 5 (TB323FU)",
        "Lenovo Legion Tab Y700 (TB323FU)",
    ];
    for alias in aliases {
        let fixture: Fixture = Fixture::new();
        fixture.hidraw("hidraw6", G9_HID_ID, &GCM_DESCRIPTOR);
        let output: std::process::Output = fixture
            .packaged_command(alias)
            .arg("supported")
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{alias} should be covered by the packaged profile: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}
