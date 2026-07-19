#!/bin/zsh
set -euo pipefail

PROJECT_DIR=${0:A:h}
SDKCONFIG="$PROJECT_DIR/sdkconfig"

if [[ ! -f "$SDKCONFIG" ]]; then
  print -u2 "未找到 $SDKCONFIG，请先在工程中执行一次 idf.py reconfigure。"
  exit 1
fi

print -n "请输入 BUPT-portal 校园网账号："
IFS= read -r BUPT_ACCOUNT
print -n "请输入 BUPT-portal 校园网密码（输入时不显示）："
IFS= read -rs BUPT_PASSWORD
print

if [[ -z "$BUPT_ACCOUNT" || -z "$BUPT_PASSWORD" ]]; then
  print -u2 "账号和密码不能为空。"
  exit 1
fi

export BUPT_ACCOUNT BUPT_PASSWORD SDKCONFIG
/usr/bin/python3 <<'PY'
import json
import os
import re
from pathlib import Path

path = Path(os.environ["SDKCONFIG"])
text = path.read_text(encoding="utf-8")

values = {
    "CONFIG_FOCUSCUBE_WIFI_SSID": "BUPT-portal",
    "CONFIG_FOCUSCUBE_WIFI_PASSWORD": "",
    "CONFIG_FOCUSCUBE_BUPT_ACCOUNT": os.environ["BUPT_ACCOUNT"],
    "CONFIG_FOCUSCUBE_BUPT_PASSWORD": os.environ["BUPT_PASSWORD"],
}

for key, value in values.items():
    replacement = f"{key}={json.dumps(value, ensure_ascii=False)}"
    text, count = re.subn(rf"^{re.escape(key)}=.*$", replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"配置项 {key} 不存在或重复，未写入账号密码。")

path.write_text(text, encoding="utf-8")
PY

unset BUPT_ACCOUNT BUPT_PASSWORD
print "已写入本机 sdkconfig。该文件已加入 .gitignore，不会被 Git 默认追踪。"
