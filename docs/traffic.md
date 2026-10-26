# 动态抓包

环境是 MuMu 模拟器。踩了几个坑，记下来。

---

## 1. 连设备

```powershell
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell "su -c id"
```

出 `uid=0(root)` 就对了。

**注意**：MuMu 会同时暴露 `emulator-5554` 和 `127.0.0.1:5555`。用 5555 那个，`emulator-5554` 经常是 offline 状态。

设备信息：

```
型号      V2309A
Android   12 / SDK 32
ABI       x86_64
SELinux   Permissive
Root      su 直接可用
```

有 `libhoudini.so`，ARM 转译层在。所以只带 arm64 so 的 App 也能跑。

---

## 2. frida 在这环境会崩

先说结论：**别用。**

attach 上去进程立刻 SIGSEGV。看 tombstone：

```
signal 11 (SIGSEGV), code -6 (SI_TKILL)
backtrace:
  #00 linker64!soinfo::get_realpath()
  #01 /memfd:frida-agent-64.so
  #02 /memfd:frida-agent-64.so
  #03 /memfd:frida-agent-64.so
  ...
```

崩溃时寄存器全 0：

```
rax 0000000000000000  rdi 0000000000000000
rbx 0000000000000000  rsi 0000000000000000
```

调用方传进来的 soinfo 指针是空的，`get_realpath()` 直接解引用。

**根因**：houdini 把 arm64 的 so 映射进 x86_64 进程，linker 内部的 soinfo 结构里有些字段是转译层伪造的。frida agent 初始化时要遍历 so 列表、给每个 so 取路径，走到那个伪造结构上就炸了。

**不是壳在拦。** 我一开始以为是检测，查完发现壳的 `/proc/self/maps` 检查根本没触发，frida 没初始化完就死了。

**降版本没用**。17.17 和 16.7.19 崩在同一个地址（`linker64+0x62d83`）。

**参数也没用**。`-C`（ignore-crashes）、`-P`（disable-preload）、两个一起，都试过：

```
-C       ProcessNotRespondingError: refused to load frida-agent
-P       ProcessNotRespondingError: unexpected early end-of-stream
-C -P    ProcessNotRespondingError: timed out trying to sync up with agent
```

结论：**houdini 环境跳过 frida。** 这不是能调好的问题。

补充一下 frida 的部署（万一以后在别的模拟器上要用）：

```bash
# 下载 frida-server-<ver>-android-x86_64.xz 注意选 x86_64
adb push fs /data/local/tmp/fs
adb shell su -c "chmod 755 /data/local/tmp/fs"
adb shell su -c "cd /data/local/tmp && nohup ./fs -l 0.0.0.0:27042 >/data/local/tmp/fs.log 2>&1 &"
adb forward tcp:27042 tcp:27042      # 这步不能忘，忘了就连不上
```

本机 python 侧的 frida 版本必须和 server 一致，否则 `ProtocolError: please ensure that major versions match`。

---

## 3. 抓包：别设全局代理

第一反应是 `settings put global http_proxy 10.0.2.2:8080`。

**不行。** App 里有 WebView，它有自己的信任链，全局代理一设就报「无法访问页面」。而且 App 的首页会被这个错误页盖住，UI 都点不动。

正确做法是用 iptables 只劫持业务域名。

### 先装证书

```bash
# 设备上
adb shell su -c "sh /data/local/tmp/install_cert.sh"
```

内容就是把 `/data/local/tmp/mitm.pem` 拷进系统证书目录，然后 bind-mount 覆盖：

```sh
rm -rf /data/local/tmp/cacerts
mkdir -p /data/local/tmp/cacerts
cp -r /system/etc/security/cacerts/* /data/local/tmp/cacerts/
cp /data/local/tmp/mitm.pem /data/local/tmp/cacerts/c8750f0d.0
chmod 644 /data/local/tmp/cacerts/c8750f0d.0
CTX=$(ls -Zd /system/etc/security/cacerts | awk '{print $1}')
chcon -R "$CTX" /data/local/tmp/cacerts
mount -o bind /data/local/tmp/cacerts /system/etc/security/cacerts
```

验证：

```bash
mount | grep cacerts
# /dev/block/sdc3 on /system/etc/security/cacerts type ext4
ls /system/etc/security/cacerts | wc -l     # 原 129 + 1 = 130
```

### adb reverse

让设备的 8080 打到 PC 的 8080：

```bash
adb reverse tcp:8080 tcp:8080
```

### 只劫持业务域名

先解析业务域名拿 IP：

```bash
adb shell "ping -c 1 game-xacc.xunlei.com | head -1"
# 47.102.154.248
```

然后 REDIRECT 整个 /24：

```bash
adb shell su -c "iptables -t nat -A OUTPUT -p tcp -d 47.102.154.0/24 --dport 443 -j REDIRECT --to-ports 8080"
adb shell su -c "iptables -t nat -A OUTPUT -p tcp -d 47.102.199.0/24 --dport 443 -j REDIRECT --to-ports 8080"
adb shell su -c "iptables -t nat -L OUTPUT -n -v"    # 确认
```

广告 SDK（`v2mi.gdt.qq.com`、`api-access.pangolin-sdk-toutiao.com`）和 WebView 都走直连，不受影响。

### PC 侧

```bash
mitmdump --listen-host 0.0.0.0 --listen-port 8080 -w capture.mitm
```

**注意端口占用**。上一轮 mitmdump 没退干净会报 `[Errno 10048]`：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8080 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

### 收尾

```bash
adb shell su -c "iptables -t nat -F OUTPUT"
adb shell "settings put global http_proxy :0"
adb shell su -c "umount /system/etc/security/cacerts"
```

---

## 4. 读 flow

```python
# 用系统 python（装了 mitmproxy 的那个），不是 venv
from mitmproxy.io import FlowReader

with open('capture.mitm', 'rb') as f:
    for fl in FlowReader(f).stream():
        print(fl.request.method, fl.request.pretty_url[:130])
        if 'TaskList' in fl.request.pretty_url:
            print(fl.response.get_text(strict=False)[:2000])
```

---

## 5. UI 自动化的坑

**uiautomator 抓不到 RN 界面。**

```bash
adb shell uiautomator dump /sdcard/ui.xml
adb pull /sdcard/ui.xml
```

导出后只有桌面或者 WebView 的节点，App 自己的界面一个没有。因为 React Native 是自绘的，无障碍树里没内容。

只能靠截图 + 人眼 + 硬坐标。

**截图必须走文件，不能 exec-out**：

```bash
# 错，会毁文件
adb exec-out screencap -p > x.png
# 对
adb shell screencap -p /sdcard/x.png
adb pull /sdcard/x.png
```

因为 `exec-out` 会把 `\n` 转成 `\r\n`，PNG 里全是 `0x0a` 字节，整个文件就毁了。文件头 `89 50 4e 47 0d 0a 1a 0a` 里就有一个 `0a`。

**判断点击是否真的生效，用 hash 对比**：

```python
import hashlib, subprocess

def snapshot():
    subprocess.run(['adb','-s','127.0.0.1:5555','shell','screencap -p /sdcard/_s.png'])
    subprocess.run(['adb','-s','127.0.0.1:5555','pull','/sdcard/_s.png','_s.png'])
    return hashlib.md5(open('_s.png','rb').read()).hexdigest()[:12]

before = snapshot()
tap(300, 560)
after = snapshot()
print('changed =', before != after)
```

比"截图看一眼感觉没变"靠谱。有时候点到了但页面变化很小，肉眼看不出来。

---

## 6. 权限弹窗

第一次进会弹「开启应用列表权限」，要 `QUERY_ALL_PACKAGES`。

点取消就行了，不影响任务功能。点了确认反而会真的去扫你手机上装了啥。

坐标自己从截图里量，不同分辨率不一样。
