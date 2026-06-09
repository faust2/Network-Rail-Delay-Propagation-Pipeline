#print("Meow")

from __future__ import annotations

import os
from pathlib import Path
import requests

BASE_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"
DEFAULT_TYPE = "CIF_ALL_FULL_DAILY"
DEFAULT_DAY = "toc-full"


def download_schedule_file(
    username: str,
    password: str,
    output_path: Path,
    feed_type: str = DEFAULT_TYPE,
    day: str = DEFAULT_DAY,
    timeout: int = 120,
) -> Path:
    """
    Download the Network Rail SCHEDULE JSON gzip file.

    Notes:
    - The SCHEDULE feed is a static feed accessed via authenticated HTTP GET.
    - Successful authentication returns an HTTP 302 redirect to the actual file.
    - The JSON format is recommended for users starting out.

    Raises:
        requests.HTTPError on HTTP failure.
    """
    params = {"type": feed_type, "day": day}
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(
        BASE_URL,
        params=params,
        auth=(username, password),
        allow_redirects=True,
        timeout=timeout,
        stream=True,
    ) as response:
        response.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    return output_path


def main() -> None:
    username = "cblaxlandkay@gmail.com"
    password = "Hyperion00!!"
    #username = os.environ.get("NR_USERNAME")
    #password = os.environ.get("NR_PASSWORD")
    #["cblaxlandkay@gmail.com", "Hyperion00!!"]
    if not username or not password:
        raise ValueError(
            "Set NR_USERNAME and NR_PASSWORD as environment variables first."
        )

    output_path = Path("data/raw/schedule_full.json.gz")
    path = download_schedule_file(username, password, output_path)
    print(f"Downloaded schedule file to: {path.resolve()}")


if __name__ == "__main__":
    main()