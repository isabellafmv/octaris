#!/usr/bin/env python3
"""Octaris — full build, on macOS or Windows.

Builds the complete desktop app:
  1. PyInstaller bundles the Python/FastAPI backend into a standalone folder
     (on macOS, build.py then signs every binary in it)
  2. electron-builder packages Electron + the backend (macOS .dmg/.zip,
     Windows NSIS installer); on macOS it signs and notarizes the app
  3. macOS: build.py signs, notarizes and staples the .dmg, then checks the
     app and the .dmg the way Gatekeeper will

Prerequisites (in the Python environment you run this with):
  pip install pyinstaller
  Node.js 20 with npm

Usage:
  python build.py [--unsigned] [electron-builder options]

Options are passed on to electron-builder, e.g. `python build.py --publish never`
(CI does that on tags, where electron-builder would otherwise try to publish a
GitHub release).

macOS builds are signed with a "Developer ID Application" certificate from the
keychain (CSC_NAME picks one) and notarized with APPLE_ID,
APPLE_APP_SPECIFIC_PASSWORD and APPLE_TEAM_ID; see "Signed macOS build" in the
README. --unsigned (or OCTARIS_UNSIGNED=1) builds an ad-hoc signed app instead,
for local testing; other Macs only open that after `xattr -cr`.

The installers end up in client/dist/.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
CLIENT = ROOT / "client"

# Per OS: the resources/bin folder (config.json's "target"), the binaries that
# must be in it, the npm script that packages the app, and its outputs.
# CuraEngine's libraries, next to it (it finds them through @executable_path)
MAC_CURA_LIBS = [
    "libArcus.dylib",
    "libpolyclipping.22.dylib",
    "libtbb.12.dylib",
    "libtbbmalloc.2.dylib",
    "libtbbmalloc_proxy.2.dylib",
]
PLATFORMS = {
    "darwin": ("macos", ["CuraEngine", "UltiMaker-Cura", *MAC_CURA_LIBS], "build:mac", ["*.dmg", "*.zip"]),
    "win32": ("windows", ["CuraEngine.exe"], "build:win", ["*.exe"]),
}

LFS_POINTER = b"git-lfs.github.com/spec"

# macOS signing
NOTARY_ENV = ("APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID")
DEVELOPER_ID = "Developer ID Application:"
BACKEND_ENTITLEMENTS = CLIENT / "build" / "entitlements.backend.plist"
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",  # 64-bit, little-endian
    b"\xce\xfa\xed\xfe": "<",  # 32-bit, little-endian
    b"\xca\xfe\xba\xbe": ">",  # universal (fat)
}
MH_EXECUTE = 2


def fail(message: str) -> NoReturn:
    print(f"✗ {message}", file=sys.stderr)
    sys.exit(1)


def step(message: str) -> None:
    print(f"\n▸ {message}", flush=True)


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        fail(f"{cmd[0]} failed (exit code {result.returncode})")


def output(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def check_binaries(target: str, names: list[str]) -> None:
    """Fail early on a missing CuraEngine, or a Git LFS pointer in its place
    (without `git lfs pull` that's what a checkout has, and PyInstaller would
    happily bundle the text file)."""
    bin_dir = ROOT / "resources" / "bin" / target
    for name in names:
        path = bin_dir / name
        if not path.is_file():
            hint = (
                "Copy CuraEngine.exe (and the DLLs next to it) from an UltiMaker Cura 5 install, "
                r"e.g. C:\Program Files\UltiMaker Cura 5.x.x\, into resources\bin\windows\. See README."
                if target == "windows"
                else "See README."
            )
            fail(f"Missing {path.relative_to(ROOT)}. {hint}")
        with path.open("rb") as f:
            if LFS_POINTER in f.read(64):
                fail(f"{path.relative_to(ROOT)} is a Git LFS pointer. Run: git lfs install && git lfs pull")
    if target == "macos":
        check_load_paths(bin_dir)


def check_load_paths(bin_dir: Path) -> None:
    """Fail on a binary that loads a library from outside the app: a library
    path or rpath that is absolute and not the system's. A CuraEngine built
    with conan points into ~/.conan2, which works only on the Mac that built
    it (see README)."""
    for path in sorted(bin_dir.iterdir()):
        if not path.is_file() or macho_kind(path) is None:
            continue
        load = output(["otool", "-L", str(path)]).stdout.splitlines()[1:]
        libs = [line.split(" (")[0].strip() for line in load]
        commands = output(["otool", "-l", str(path)]).stdout.splitlines()
        rpaths = [
            commands[i + 2].split()[1] for i, line in enumerate(commands) if line.strip() == "cmd LC_RPATH"
        ]
        outside = [
            p for p in libs + rpaths if p.startswith("/") and not p.startswith(("/usr/lib/", "/System/"))
        ]
        if outside:
            fail(
                f"{path.relative_to(ROOT)} loads libraries from outside the app:\n    "
                + "\n    ".join(outside)
                + "\n  Copy them next to it and use @executable_path / @loader_path (see README)."
            )


def npm() -> str:
    # npm is npm.cmd on Windows, which subprocess only finds by its full name
    found = shutil.which("npm")
    if found is None:
        fail("npm not found. Install Node.js 20.")
    return found


def find_identity() -> tuple[str, str]:
    """The (SHA-1 hash, name) of the Developer ID Application certificate to
    sign with: the only one in the keychain, or the one CSC_NAME names."""
    result = output(["security", "find-identity", "-v", "-p", "codesigning"])
    identities = []
    for line in result.stdout.splitlines():
        # '  1) F797E8...A7 "Developer ID Application: Name (TEAMID)"'
        parts = line.strip().split(" ", 2)
        if len(parts) == 3 and DEVELOPER_ID in parts[2]:
            identities.append((parts[1], parts[2].strip('"')))
    wanted = os.environ.get("CSC_NAME", "").removeprefix(DEVELOPER_ID).strip()
    if wanted:
        identities = [(h, name) for h, name in identities if wanted in name or wanted.upper() == h]
    if len(identities) != 1:
        found = "\n".join(f"    {h}  {name}" for h, name in identities) or "    (none)"
        fail(
            f"Need exactly one '{DEVELOPER_ID.rstrip(':')}' certificate"
            + (f" matching CSC_NAME={wanted!r}" if wanted else "")
            + f", found:\n{found}\n"
            "  Install it in the login keychain (see README, 'Signed macOS build'), set CSC_NAME\n"
            "  to choose between several, or build unsigned with --unsigned."
        )
    return identities[0]


def macho_kind(path: Path) -> str | None:
    """'exe' or 'lib' for a Mach-O file, None for anything else."""
    with path.open("rb") as f:
        head = f.read(32)
        endian = MACHO_MAGICS.get(head[:4])
        if endian is None:
            return None
        if endian == ">":
            # Universal binary: look at the first architecture's header. Java
            # class files share this magic but have a huge "architecture count".
            count, offset = int.from_bytes(head[4:8], "big"), int.from_bytes(head[16:20], "big")
            if not 0 < count < 32:
                return None
            f.seek(offset)
            head = f.read(16)
            if head[:4] not in MACHO_MAGICS:
                return None
        filetype = int.from_bytes(head[12:16], "little")
    return "exe" if filetype == MH_EXECUTE else "lib"


def sign_backend(bundle: Path, identity: str) -> None:
    """Sign every Mach-O file in the PyInstaller output (the bootloader,
    libpython and the other dylibs, the extension modules, CuraEngine) with
    the Developer ID, the hardened runtime and a secure timestamp, deepest
    first. electron-builder leaves Resources/backend alone (signIgnore), and
    the app's own signature then seals these files as they are."""
    binaries: dict[Path, str] = {}
    for path in bundle.rglob("*"):
        if path.is_file() and not path.is_symlink() and (kind := macho_kind(path)):
            binaries[path] = kind
    # Same depth: one codesign call per kind (it signs several files at once)
    by_depth: dict[tuple[int, str], list[Path]] = {}
    for path, kind in binaries.items():
        by_depth.setdefault((len(path.relative_to(bundle).parts), kind), []).append(path)
    base = ["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity]
    for depth, kind in sorted(by_depth, reverse=True):
        paths = sorted(by_depth[depth, kind])
        cmd = base + (["--entitlements", str(BACKEND_ENTITLEMENTS)] if kind == "exe" else [])
        result = output(cmd + [str(p) for p in paths])
        if result.returncode != 0:
            fail(f"codesign failed:\n{result.stderr}")
    print(f"✓ Signed {len(binaries)} binaries in {bundle.relative_to(ROOT)}")


def notarize_dmg(dmg: Path, identity: str) -> None:
    """electron-builder notarizes and staples the .app; the .dmg around it
    gets its own signature, notarization and ticket, so Gatekeeper accepts a
    downloaded .dmg offline too."""
    run(["codesign", "--force", "--timestamp", "--sign", identity, str(dmg)], ROOT)
    print(f"  $ xcrun notarytool submit {dmg.name} --wait  (takes a few minutes)", flush=True)
    result = output(
        ["xcrun", "notarytool", "submit", str(dmg), "--wait", "--output-format", "json",
         "--apple-id", os.environ["APPLE_ID"], "--password", os.environ["APPLE_APP_SPECIFIC_PASSWORD"],
         "--team-id", os.environ["APPLE_TEAM_ID"]]
    )  # fmt: skip
    try:
        submission = json.loads(result.stdout)
    except json.JSONDecodeError:
        submission = {}
    if "id" not in submission:
        # e.g. wrong credentials: no submission was made
        fail(f"notarytool failed:\n{result.stdout}{result.stderr}")
    if submission.get("status") != "Accepted":
        log = output(
            ["xcrun", "notarytool", "log", submission["id"],
             "--apple-id", os.environ["APPLE_ID"], "--password", os.environ["APPLE_APP_SPECIFIC_PASSWORD"],
             "--team-id", os.environ["APPLE_TEAM_ID"]]
        )  # fmt: skip
        fail(f"Notarization of {dmg.name}: {submission.get('status')}\n{log.stdout}{log.stderr}")
    print(f"✓ Notarized {dmg.name}")
    run(["xcrun", "stapler", "staple", str(dmg)], ROOT)


def verify_mac(app: Path, dmg: Path | None, signed: bool) -> None:
    """Check the result the way another Mac will, print each check's output,
    and fail if any of them fails."""
    checks = [["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)]]
    if signed:
        checks.append(["spctl", "-a", "-vv", "-t", "exec", str(app)])
        if dmg is not None:
            checks.append(["xcrun", "stapler", "validate", str(dmg)])
    failed = []
    for cmd in checks:
        result = output(cmd)
        print(f"\n  $ {' '.join(cmd)}")
        for line in (result.stdout + result.stderr).strip().splitlines():
            print(f"    {line}")
        if result.returncode != 0:
            failed.append(cmd[0] if cmd[0] != "xcrun" else "stapler")
    if signed and dmg is None:
        failed.append("no .dmg built")
    if failed:
        fail(f"macOS signing checks failed: {', '.join(failed)}")
    print("\n✓ Signature checks passed")


def main() -> None:
    if sys.platform not in PLATFORMS:
        fail(f"Building on {sys.platform} isn't supported (macOS and Windows only).")
    target, binaries, npm_script, outputs = PLATFORMS[sys.platform]
    builder_args = [arg for arg in sys.argv[1:] if arg != "--unsigned"]
    mac = sys.platform == "darwin"
    signed = mac and "--unsigned" not in sys.argv[1:] and os.environ.get("OCTARIS_UNSIGNED") != "1"

    print("═══════════════════════════════════════════")
    print(f"  Octaris Build ({target})")
    print("═══════════════════════════════════════════")

    check_binaries(target, binaries)

    # Fail before the slow part, not after it
    builder_env = dict(os.environ)
    if signed:
        identity, identity_name = find_identity()
        print(f"Signing as: {identity_name}")
        missing = [name for name in NOTARY_ENV if not os.environ.get(name)]
        if missing:
            fail(f"Set {', '.join(missing)} to notarize (see README), or build unsigned with --unsigned.")
        # The exact certificate, so electron-builder can't pick another one
        builder_env["CSC_NAME"] = identity
    elif mac:
        print("Unsigned build (ad-hoc signature, not notarized)")
        # No Developer ID: on Apple Silicon electron-builder then signs ad hoc,
        # which keeps the bundle's signature valid. Without the hardened
        # runtime, since an ad-hoc signature has no Team ID for library
        # validation to compare.
        builder_env["CSC_IDENTITY_AUTO_DISCOVERY"] = "false"
        for name in (*NOTARY_ENV, "CSC_NAME", "CSC_LINK"):
            builder_env.pop(name, None)
        builder_args += ["-c.mac.hardenedRuntime=false", "-c.mac.notarize=false"]

    # ── Step 1: the backend, with PyInstaller ───────────────────────────────
    step("Building backend...")
    # PyInstaller can only bundle packages installed in the Python running it
    # Without the [dev] extras: the app needs none of them (octaris-backend.spec
    # also excludes them, for a Python that has them installed anyway)
    run([sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"], BACKEND)
    for folder in ("build", "dist"):
        shutil.rmtree(BACKEND / folder, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "octaris-backend.spec", "--noconfirm"], BACKEND)

    bundle = BACKEND / "dist" / "octaris-backend"
    executable = bundle / ("octaris-backend.exe" if sys.platform == "win32" else "octaris-backend")
    if not executable.exists():
        fail(f"PyInstaller didn't produce {executable.relative_to(ROOT)}")
    print(f"✓ Backend built: {executable.relative_to(ROOT)}")

    if sys.platform != "win32":
        # PyInstaller doesn't preserve +x on data files
        for name in binaries:
            path = bundle / "_internal" / "resources" / "bin" / target / name
            if path.exists():
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if signed:
        step("Signing backend...")
        sign_backend(bundle, identity)

    # ── Step 2: the Electron app ────────────────────────────────────────────
    step("Building Electron app...")
    if not (CLIENT / "node_modules").is_dir():
        run([npm(), "install"], CLIENT)
    # Old outputs would end up in the checks below
    shutil.rmtree(CLIENT / "dist", ignore_errors=True)
    run([npm(), "run", npm_script, "--", *builder_args], CLIENT, builder_env)

    if mac:
        app = next((CLIENT / "dist" / "mac-arm64").glob("*.app"), None)
        dmg = next((CLIENT / "dist").glob("*.dmg"), None)
        if app is None:
            fail("electron-builder didn't produce an .app in client/dist/mac-arm64")
        if signed and dmg is not None:
            step("Notarizing the .dmg...")
            notarize_dmg(dmg, identity)
        step("Checking signatures...")
        verify_mac(app, dmg, signed)

    print("\n═══════════════════════════════════════════")
    print("  Build complete!")
    print("═══════════════════════════════════════════\n")
    print("Outputs in client/dist/:")
    for pattern in outputs:
        for path in sorted((CLIENT / "dist").glob(pattern)):
            print(f"  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")

    if signed:
        print("\nSigned, notarized and stapled: opens on other Macs with a double-click.")
        if any(arg.startswith("--publish") for arg in builder_args) and "never" not in builder_args:
            print("Note: electron-builder uploaded the .dmg before it was notarized and stapled.")
            print("Replace it in the release with client/dist's copy.")
    elif mac:
        print("\nUnsigned build. On another Mac, run this once after copying the app over:")
        print(f'  xattr -cr "/Applications/{app.name}"')
    else:
        print("\nThe installer is unsigned: SmartScreen warns on first run (More info → Run anyway).")


if __name__ == "__main__":
    # A Windows console or pipe defaults to a code page without ═ ✓ ✗
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    os.chdir(ROOT)
    main()
