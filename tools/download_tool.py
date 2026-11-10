import argparse
from pathlib import Path
from get_platform import get_platform
import zipfile
import io
import requests
import stat
import time

root_path = Path(__file__).parent.parent


platform = get_platform()
if platform is None:
    exit(1)


parser = argparse.ArgumentParser()
parser.add_argument("tool")
parser.add_argument("tag")
parser.add_argument("--path", type=Path, required=True)
args = parser.parse_args()


def dsd_url(tag: str) -> str:
    return f"https://github.com/AetiasHax/ds-decomp/releases/download/{tag}/dsd-{platform.system}-{platform.machine}{platform.exe}"


def mwccarm_url(tag: str) -> str:
    return "http://decomp.aetias.com/files/mwccarm.zip"


def wibo_url(tag: str) -> str:
    # Map platform to wibo binary name
    if platform.system == "darwin":
        binary = "wibo-macos"
    elif platform.machine == "i686" or platform.machine == "i386":
        binary = "wibo-i686"
    else:  # x86_64
        binary = "wibo-x86_64"
    return f"https://github.com/decompals/wibo/releases/download/{tag}/{binary}"


def objdiff_url(tag: str) -> str:
    return f"https://github.com/encounter/objdiff/releases/download/{tag}/objdiff-cli-{platform.system}-{platform.machine}{platform.exe}"


TOOLS = {
    "dsd": dsd_url,
    "mwccarm": mwccarm_url,
    "wibo": wibo_url,
    "objdiff": objdiff_url,
}


def fetch(url: str, attempts: int = 3) -> requests.Response:
    """GET the URL, retrying: a failed request must not leave an error page behind
    as the tool (seen in CI, 2026-11-09: objdiff-cli held a non-binary response)."""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            if response.content:
                return response
            error = "empty response"
        except requests.RequestException as e:
            error = str(e)
        print(f"Attempt {attempt}/{attempts} failed: {error}")
        if attempt < attempts:
            time.sleep(5 * attempt)
    raise SystemExit(f"Could not download {url}")


download_url = TOOLS[args.tool](args.tag)
print(f"\nDownloading {args.tool} {args.tag}...")
response = fetch(download_url)
if download_url.endswith(".zip"):
    zip_file = zipfile.ZipFile(io.BytesIO(response.content))
    zip_file.extractall(args.path)
else:
    out_path: Path = args.path
    with out_path.open("wb") as f:
        f.write(response.content)
    out_path.chmod(out_path.stat().st_mode | stat.S_IEXEC)
