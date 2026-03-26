import time
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

try:
    import uiautomator2 as u2
except ImportError:
    u2 = None


def safe_sleep(duration):
    time.sleep(duration)


def validate_pairing_code(code: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (code or "")).upper()
    if len(cleaned) != 8:
        raise ValueError(
            "Pairing code must contain exactly 8 letters/numbers (e.g. JH58-7TX2 or 1234-5678)."
        )
    return cleaned


def check_adb_available():
    try:
        subprocess.check_output(["adb", "version"], stderr=subprocess.STDOUT)
    except FileNotFoundError:
        raise SystemExit("❌ adb is not installed or not in PATH.")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"❌ adb is not available: {exc}")


def ensure_screen_unlocked(device):
    device.screen_on()
    time.sleep(0.5)

    try:
        info = device.info
    except Exception:
        info = {}

    pkg = info.get("currentPackageName")
    if pkg == "com.android.systemui":
        w = info.get("displayWidth") or 1080
        h = info.get("displayHeight") or 1920
        device.swipe(w * 0.5, h * 0.8, w * 0.5, h * 0.3, 0.2)
        time.sleep(1)

    try:
        if device.info.get("currentPackageName") == "com.android.systemui":
            device.unlock()
            time.sleep(0.5)
    except Exception:
        pass


def enter_code_on_phone(device, code: str) -> bool:
    if not code:
        return False

    clean_code = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    print(f"📲 Entering code on phone: {code} (sending as {clean_code})")

    try:
        edit_texts = device(className="android.widget.EditText")
        if edit_texts.exists(timeout=2):
            edit_texts[0].click()
            time.sleep(0.5)
            device.shell(f"input text '{clean_code}'")
            print("✅ Code entered via ADB input text")
            return True

        time.sleep(1)
        device.shell(f"input text '{clean_code}'")
        print("✅ Code entered via ADB input text (fallback)")
        return True
    except Exception as e:
        print(f"⚠️ Failed to enter code: {e}")
        return False


def detect_buttons(device):
    xml = device.dump_hierarchy()
    root = ET.fromstring(xml)
    buttons = []

    def center(bounds):
        nums = list(map(int, re.findall(r"\d+", bounds)))
        if len(nums) == 4:
            x1, y1, x2, y2 = nums
            return (x1 + x2) // 2, (y1 + y2) // 2
        return None

    def walk(node):
        a = node.attrib
        pkg = a.get("package", "")
        cls = a.get("class", "") or ""
        text_raw = a.get("text", "") or ""
        desc_raw = a.get("content-desc", "") or a.get("content_desc", "") or ""
        res_raw = a.get("resource-id", "") or ""
        text = text_raw.strip().lower()
        desc = desc_raw.strip().lower()
        res = res_raw.strip().lower()
        bounds = a.get("bounds", "")
        clickable = a.get("clickable", "false")

        if bounds and (clickable == "true" or "button" in cls.lower() or text or desc or res):
            c = center(bounds)
            if c:
                buttons.append({
                    "pkg": pkg,
                    "class": cls,
                    "text": text,
                    "text_raw": text_raw,
                    "desc": desc,
                    "desc_raw": desc_raw,
                    "res": res,
                    "res_raw": res_raw,
                    "x": c[0],
                    "y": c[1]
                })

        for ch in node:
            walk(ch)

    walk(root)
    return buttons


def clear_recent_apps(device, max_swipes=10):
    print("🧹 Clearing recent apps")
    device.shell("input keyevent KEYCODE_APP_SWITCH")
    time.sleep(2)

    for _ in range(max_swipes):
        buttons = detect_buttons(device)
        cards = [b for b in buttons if "unlocked" in b["desc"]]

        if not cards:
            print("✅ Recent apps cleared")
            break

        b = cards[0]
        device.shell(
            f"input swipe {b['x']} {b['y']} {b['x'] - 700} {b['y']} 200"
        )
        time.sleep(0.5)

    device.shell("input keyevent KEYCODE_HOME")
    time.sleep(1)


def handle_app_chooser(device, pkg, user_id, timeout=6):
    print("🔎 Checking for app chooser…")
    end = time.time() + timeout

    while time.time() < end:
        buttons = detect_buttons(device)
        chooser = [
            b for b in buttons
            if b["pkg"] in ("android", "com.android.systemui") and b["text"]
        ]

        if len(chooser) >= 2:
            chooser.sort(key=lambda x: x["y"])

            if pkg == "com.whatsapp.w4b":
                app_name = "WhatsApp Business"
            else:
                app_name = "WhatsApp"

            if user_id != 0:
                target = chooser[1]
                print(f"✅ Selecting DUAL {app_name} (2nd option)")
            else:
                target = chooser[0]
                print(f"✅ Selecting NORMAL {app_name} (1st option)")

            try:
                device.click(target['x'], target['y'])
            except Exception:
                device.shell(f"input tap {target['x']} {target['y']}")
            time.sleep(1)
            return True

        time.sleep(0.4)

    print("ℹ️ No chooser dialog detected")
    return False


def wait_for_whatsapp(device, pkg, timeout=20):
    print("⏳ Waiting for WhatsApp to be ready...")
    end = time.time() + timeout

    while time.time() < end:
        cur = device.app_current()
        if cur and cur.get("package") == pkg:
            print("✅ WhatsApp foreground (app_current)")
            return True

        buttons = detect_buttons(device)
        wa_ui = [
            b for b in buttons
            if b["pkg"] == pkg and (
                "menuitem_overflow" in b["res"]
                or "new chat" in b["text"]
                or "chats" in b["text"]
            )
        ]

        if wa_ui:
            print("✅ WhatsApp UI detected (dual-safe)")
            return True

        safe_sleep(0.6)

    return False


def smart_click(device, package_name, keywords, timeout=8):
    end = time.time() + timeout

    while time.time() < end:
        buttons = detect_buttons(device)
        wa = [
            b
            for b in buttons
            if b.get("pkg") == package_name or package_name in (b.get("pkg") or "")
        ]

        for b in wa:
            if b.get('res') and any(k in b['res'] for k in keywords):
                try:
                    rid = b.get('res_raw')
                    if rid and device(resourceId=rid).exists(timeout=0.8):
                        device(resourceId=rid).click()
                        return True
                except Exception:
                    pass
                try:
                    device.click(b['x'], b['y'])
                    return True
                except Exception:
                    pass

        for b in wa:
            try:
                txt = b.get('text_raw')
                if txt and any(k in b.get('text', '') for k in keywords):
                    if device(text=txt).exists(timeout=0.8):
                        device(text=txt).click()
                        return True
                    else:
                        device.click(b['x'], b['y'])
                        return True
            except Exception:
                pass

        for b in wa:
            try:
                dsc = b.get('desc_raw')
                if dsc and any(k in b.get('desc', '') for k in keywords):
                    if device(description=dsc).exists(timeout=0.8):
                        device(description=dsc).click()
                        return True
                    else:
                        device.click(b['x'], b['y'])
                        return True
            except Exception:
                pass

        time.sleep(0.4)

    return False


def get_android_users():
    out = subprocess.check_output(
        ["adb", "shell", "pm", "list", "users"],
        universal_newlines=True,
        stderr=subprocess.STDOUT,
    )
    users = []
    for line in out.splitlines():
        m = re.search(r'UserInfo\{(\d+):', line)
        if m:
            users.append(int(m.group(1)))
    return users


def format_instance_label(pkg, user):
    if pkg == "com.whatsapp" and user == 0:
        return "WhatsApp (Normal)"
    if pkg == "com.whatsapp" and user != 0:
        return f"WhatsApp Dual (user {user})"
    if pkg == "com.whatsapp.w4b" and user == 0:
        return "WhatsApp Business"
    if pkg == "com.whatsapp.w4b" and user != 0:
        return f"WhatsApp Business Dual (user {user})"
    return f"{pkg} (user {user})"


def main():
    if u2 is None:
        raise SystemExit("❌ Missing dependency: install with `pip install uiautomator2`.")

    check_adb_available()

    if len(sys.argv) > 1:
        pairing_code_input = sys.argv[1].strip()
    else:
        pairing_code_input = input("Enter pairing code (XXXX-XXXX): ").strip()

    if not pairing_code_input:
        raise SystemExit("❌ Pairing code is required.")

    try:
        pairing_code = validate_pairing_code(pairing_code_input)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}")

    # Connect to device
    try:
        d = u2.connect()
        ensure_screen_unlocked(d)
    except Exception as e:
        raise SystemExit(f"❌ Failed to connect to device: {e}")

    # Detect WhatsApp instances
    instances = []
    users = get_android_users()
    packages = ["com.whatsapp", "com.whatsapp.w4b"]

    for pkg in packages:
        for user in users:
            try:
                subprocess.check_output(
                    ["adb", "shell", "pm", "path", "--user", str(user), pkg],
                    stderr=subprocess.DEVNULL,
                )
                instances.append((pkg, user))
            except subprocess.CalledProcessError:
                pass

    if not instances:
        raise SystemExit("❌ No WhatsApp found on device")

    print("\n📱 WhatsApp instances found:\n")
    for i, (pkg, user) in enumerate(instances, start=1):
        label = format_instance_label(pkg, user)
        print(f"{i}. {label}")

    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        choice = int(sys.argv[2])
        print(f"\n👉 Auto-selected WhatsApp index from arg: {choice}")
    else:
        try:
            choice_input = input("\n👉 Select WhatsApp to open: ").strip()
            if choice_input and choice_input[0].isdigit():
                choice = int(re.match(r"\d+", choice_input).group())
            else:
                choice = int(choice_input)
        except Exception:
            choice = 1
            print("⚠️ Input error, defaulting to 1")

    if 1 <= choice <= len(instances):
        package_name, user_id = instances[choice - 1]
    else:
        package_name, user_id = instances[0]
        print("⚠️ Invalid choice, defaulting to 1")

    selected_label = format_instance_label(package_name, user_id)
    print(f"\n✅ Selected: {selected_label} [{package_name} user {user_id}]\n")

    clear_recent_apps(d)

    print("🛑 Force-stopping WhatsApp")
    d.shell(f"am force-stop --user {user_id} {package_name}")
    safe_sleep(1)

    print("📱 Opening WhatsApp…")
    d.shell(f"am start --user {user_id} -n {package_name}/com.whatsapp.Main")

    handle_app_chooser(d, package_name, user_id)

    if not wait_for_whatsapp(d, package_name):
        print("🔁 Retry opening WhatsApp once…")
        d.shell(f"am start --user {user_id} -n {package_name}/com.whatsapp.Main")
        safe_sleep(2)
        if not wait_for_whatsapp(d, package_name):
            raise SystemExit("❌ WhatsApp did not become ready")

    safe_sleep(2)

    print("⋮ Opening menu")
    if not smart_click(d, package_name, ["menuitem_overflow", "more"]):
        raise SystemExit("Menu not found")

    safe_sleep(1)

    print("🔗 Opening Linked devices")
    if not smart_click(d, package_name, ["linked"]):
        raise SystemExit("Linked devices not found")

    safe_sleep(2)

    print("🟢 Clicking Link a device")
    if not smart_click(d, package_name, ["link_device"]):
        raise SystemExit("Link a device not found")

    safe_sleep(2)

    print("📞 Clicking Link with phone number")
    smart_click(d, package_name, ["phone"])
    safe_sleep(3)

    print(f"\n📱 Code received: {pairing_code_input}")
    print("📲 Entering code on phone...")

    success = enter_code_on_phone(d, pairing_code)
    if success:
        print("✅ Code entered successfully. Waiting for login to complete...")
        time.sleep(10)
    else:
        print("⚠️ Could not enter code automatically. Please enter it manually.")


if __name__ == "__main__":
    main()
