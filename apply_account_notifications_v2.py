#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apply/revert per-account notification controls to AyuGram Desktop 7.0.9.

Run from the repository root:
    python apply_account_notifications.py --check
    python apply_account_notifications.py
    python apply_account_notifications.py --revert
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


FILES = {
    "settings_h": Path("Telegram/SourceFiles/ayu/ayu_settings.h"),
    "settings_cpp": Path("Telegram/SourceFiles/ayu/ayu_settings.cpp"),
    "settings_general": Path(
        "Telegram/SourceFiles/ayu/ui/settings/settings_general.cpp"
    ),
    "notifications": Path(
        "Telegram/SourceFiles/window/notifications_manager.cpp"
    ),
}

BACKUP_ROOT = Path(".account-notifications-backup")


def load_source(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError(f"{path}: unexpected UTF-8 BOM")
    text = raw.decode("utf-8")
    newline = "\r\n" if raw.count(b"\r\n") > raw.count(b"\n") // 2 else "\n"
    text = text.replace("\r\n", "\n")
    return text, newline


def save_source(path: Path, text: str, newline: str) -> None:
    data = text if newline == "\n" else text.replace("\n", "\r\n")
    path.write_bytes(data.encode("utf-8"))


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one anchor, found {count}. "
            "The source tree may not be AyuGram Desktop 7.0.9."
        )
    return text.replace(old, new, 1)


def transform_settings_h(text: str) -> str:
    marker = "accountNotificationsEnabled(uint64 userId)"
    if marker in text:
        return text

    text = replace_once(
        text,
        "\t[[nodiscard]] const std::unordered_set<int64> &shadowBanIds() const { "
        "return _shadowBanIds; }\n\n"
        "\tvoid validate();",
        "\t[[nodiscard]] const std::unordered_set<int64> &shadowBanIds() const { "
        "return _shadowBanIds; }\n\n"
        "\tvoid setAccountNotificationsEnabled(uint64 userId, bool enabled);\n"
        "\t[[nodiscard]] bool accountNotificationsEnabled(uint64 userId) const {\n"
        "\t\treturn !_mutedNotificationAccountIds.contains(userId);\n"
        "\t}\n\n"
        "\tvoid validate();",
        "ayu_settings.h public API",
    )

    text = replace_once(
        text,
        "\tstd::unordered_set<int64> _shadowBanIds;\n\n"
        "\trpl::variable<bool> _filtersEnabled = false;",
        "\tstd::unordered_set<int64> _shadowBanIds;\n"
        "\tstd::unordered_set<uint64> _mutedNotificationAccountIds;\n\n"
        "\trpl::variable<bool> _filtersEnabled = false;",
        "ayu_settings.h private storage",
    )
    return text


def transform_settings_cpp(text: str) -> str:
    if 'p["mutedNotificationAccountIds"]' not in text:
        text = replace_once(
            text,
            "\t\ttry {\n"
            "\t\t\tfrom_json(p, settings);\n"
            "\t\t} catch (...) {",
            "\t\ttry {\n"
            "\t\t\tfrom_json(p, settings);\n"
            "\t\t\tsettings._mutedNotificationAccountIds = p.value(\n"
            "\t\t\t\t\"mutedNotificationAccountIds\",\n"
            "\t\t\t\tstd::unordered_set<uint64>{});\n"
            "\t\t} catch (...) {",
            "ayu_settings.cpp load",
        )

        text = replace_once(
            text,
            "\tjson p = settings;\n\n"
            "\tstd::ofstream file;",
            "\tjson p = settings;\n"
            "\tp[\"mutedNotificationAccountIds\"] "
            "= settings._mutedNotificationAccountIds;\n\n"
            "\tstd::ofstream file;",
            "ayu_settings.cpp save",
        )

    marker = "void AyuSettings::setAccountNotificationsEnabled("
    if marker not in text:
        text = replace_once(
            text,
            "void AyuSettings::validate() {",
            "void AyuSettings::setAccountNotificationsEnabled(\n"
            "\t\tuint64 userId,\n"
            "\t\tbool enabled) {\n"
            "\tconst auto changed = enabled\n"
            "\t\t? (_mutedNotificationAccountIds.erase(userId) > 0)\n"
            "\t\t: _mutedNotificationAccountIds.insert(userId).second;\n"
            "\tif (changed) {\n"
            "\t\tsave();\n"
            "\t}\n"
            "}\n\n"
            "void AyuSettings::validate() {",
            "ayu_settings.cpp setter",
        )
    return text


def transform_notifications(text: str) -> str:
    marker = "accountNotificationsEnabled(\n\t\t\tthread->session().userId().bare)"
    if marker in text:
        return text

    return replace_once(
        text,
        "\tif (Core::Quitting()) {\n"
        "\t\treturn { SkipState::Skip };\n"
        "\t} else if (!Core::App().settings().notifyFromAll()",
        "\tif (Core::Quitting()) {\n"
        "\t\treturn { SkipState::Skip };\n"
        "\t} else if (!AyuSettings::getInstance().accountNotificationsEnabled(\n"
        "\t\t\tthread->session().userId().bare)) {\n"
        "\t\treturn { SkipState::Skip };\n"
        "\t} else if (!Core::App().settings().notifyFromAll()",
        "notifications_manager.cpp filter",
    )


HELPER = r"""
void BuildAccountNotificationToggles(AyuSectionBuilder &ayu) {
	auto *settings = &AyuSettings::getInstance();

	for (const auto &[index, account] : Core::App().domain().accounts()) {
		const auto session = account->maybeSession();
		if (!session) {
			continue;
		}

		const auto userId = session->userId().bare;
		auto title = session->user()->name();
		if (title.isEmpty()) {
			title = u"Аккаунт %1"_q.arg(QString::number(userId));
		}

		ayu.addToggle({
			.id = u"ayu/accountNotifications/"_q + QString::number(userId),
			.title = rpl::single(u"Уведомления: "_q + std::move(title)),
			.getter = [=] {
				return settings->accountNotificationsEnabled(userId);
			},
			.setter = [=](bool enabled) {
				settings->setAccountNotificationsEnabled(userId, enabled);
				if (enabled) {
					return;
				}
				for (const auto &[index, account] : Core::App().domain().accounts()) {
					const auto currentSession = account->maybeSession();
					if (currentSession
						&& currentSession->userId().bare == userId) {
						Core::App().notifications().clearFromSession(
							currentSession);
						break;
					}
				}
			},
		});
	}
}

""".lstrip("\n")


def transform_settings_general(text: str) -> str:
    if '#include "main/main_domain.h"' not in text:
        text = replace_once(
            text,
            '#include "core/application.h"\n',
            '#include "core/application.h"\n'
            '#include "data/data_user.h"\n'
            '#include "main/main_account.h"\n'
            '#include "main/main_domain.h"\n'
            '#include "main/main_session.h"\n',
            "settings_general.cpp account includes",
        )
        text = replace_once(
            text,
            '#include "window/window_controller.h"\n',
            '#include "window/notifications_manager.h"\n'
            '#include "window/window_controller.h"\n',
            "settings_general.cpp notifications include",
        )

    if "void BuildAccountNotificationToggles(" not in text:
        text = replace_once(
            text,
            "void BuildQoLToggles(SectionBuilder &builder, AyuSectionBuilder &ayu) {",
            HELPER
            + "void BuildQoLToggles("
            "SectionBuilder &builder, AyuSectionBuilder &ayu) {",
            "settings_general.cpp helper",
        )

    if "BuildAccountNotificationToggles(ayu);" not in text:
        text = replace_once(
            text,
            "\t\t.setter = &AyuSettings::setDisableNotificationsDelay,\n"
            "\t});\n\n"
            "\tayu.addSectionDivider();",
            "\t\t.setter = &AyuSettings::setDisableNotificationsDelay,\n"
            "\t});\n\n"
            "\tayu.addSectionDivider();\n"
            "\tBuildAccountNotificationToggles(ayu);\n\n"
            "\tayu.addSectionDivider();",
            "settings_general.cpp insertion point",
        )

    return text


TRANSFORMS = {
    "settings_h": transform_settings_h,
    "settings_cpp": transform_settings_cpp,
    "settings_general": transform_settings_general,
    "notifications": transform_notifications,
}


def resolve_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    missing = [str(p) for p in FILES.values() if not (root / p).is_file()]
    if missing:
        raise RuntimeError(
            "Repository root was not recognized. Missing:\n  "
            + "\n  ".join(missing)
        )
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "repo",
        nargs="?",
        default=".",
        help="AyuGramDesktop repository root (default: current directory)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--revert", action="store_true")
    args = parser.parse_args()

    root = resolve_root(args.repo)

    if args.revert:
        backup_root = root / BACKUP_ROOT
        restored = 0
        for rel in FILES.values():
            src = backup_root / rel
            dst = root / rel
            if src.is_file():
                shutil.copy2(src, dst)
                print(f"restored: {rel}")
                restored += 1
        if not restored:
            raise RuntimeError("No backups found.")
        print("Revert complete.")
        return 0

    prepared: dict[Path, tuple[str, str, str]] = {}
    changed = 0

    for key, rel in FILES.items():
        path = root / rel
        original, newline = load_source(path)
        patched = TRANSFORMS[key](original)
        prepared[rel] = (original, patched, newline)
        if patched != original:
            changed += 1
            print(f"will patch: {rel}")
        else:
            print(f"already patched: {rel}")

    print("All source anchors verified.")

    if args.check:
        print(f"Check only: {changed} file(s) would change.")
        return 0

    if not changed:
        print("Nothing to do.")
        return 0

    backup_root = root / BACKUP_ROOT
    for rel, (original, patched, newline) in prepared.items():
        if patched == original:
            continue
        source = root / rel
        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(source, backup)
        save_source(source, patched, newline)
        print(f"patched: {rel}")

    print("Patch applied successfully.")
    print(f"Backups: {backup_root}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
