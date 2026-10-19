"""Read public cache metadata without credentials; fail closed on uncertainty."""
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PREFIX = "job-alert-state-v1-"
REPOSITORY = "Charubak/Web3-Job-Bot"
REF = "refs/heads/main"


def latest_delivery_cache() -> str | None:
    query = urlencode({"key": PREFIX, "ref": REF, "per_page": 1,
                       "sort": "created_at", "direction": "desc"})
    request = Request(f"https://api.github.com/repos/{REPOSITORY}/actions/caches?{query}",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "Web3-Job-Bot-readiness"})
    # Public repository metadata only: no token, cookie, or secret is transmitted.
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("Cannot confirm delivery-cache inventory")
        body = response.read(65_537)
    if len(body) > 65_536:
        raise RuntimeError("Unexpectedly large delivery-cache inventory")
    data = json.loads(body)
    count, entries = data.get("total_count"), data.get("actions_caches")
    if type(count) is not int or count < 0 or not isinstance(entries, list):
        raise RuntimeError("Invalid delivery-cache inventory")
    if count == 0 and entries == []:
        return None
    if count > 0 and len(entries) == 1:
        entry = entries[0]
        key = entry.get("key", "") if isinstance(entry, dict) else ""
        if (isinstance(key, str) and key.startswith(PREFIX) and
                "\n" not in key and "\r" not in key and entry.get("ref") == REF):
            return key
    raise RuntimeError("Inconsistent delivery-cache inventory; initialization refused")


if __name__ == "__main__":
    key = latest_delivery_cache()
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"exists={'true' if key else 'false'}\n")
        output.write(f"key={key or ''}\n")
    print("Existing delivery history found; restoring exact newest cache." if key
          else "Confirmed no prior delivery-state cache; baseline initialization permitted.")
