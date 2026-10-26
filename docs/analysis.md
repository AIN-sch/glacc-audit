# 静态分析路径

从 APK 到能读的 JS，中间踩过的东西记一下。

---

## 1. 先看结构

```python
import zipfile
with zipfile.ZipFile('base.apk') as z:
    names = z.namelist()

# 壳的特征
[ n for n in names if 'libDexHelper' in n ]
# lib/arm64-v8a/libDexHelper.so
# lib/arm64-v8a/libDexHelper-x86.so
# lib/armeabi-v7a/libDexHelper.so

[ n for n in names if 'meta-data' in n ]
# assets/meta-data/manifest.mf    逐文件摘要表
# assets/meta-data/rsa.pub
# assets/meta-data/rsa.sig
```

`AndroidManifest.xml` 里：

```xml
<application android:name="com.secneo.apkwrapper.AW"
             android:appComponentFactory="com.secneo.apkwrapper.AP">
```

SecNeo，梆梆企业版。国内一堆 App 用它。

---

## 2. classes.dex 到底装了啥

打开一看 108MB，`dex\n037`，header 说 `file_size` 就是 108MB，`class_defs` 只有 7 个。

7 个类：

```
Lcom/secneo/apkwrapper/AP;
Lcom/secneo/apkwrapper/AW;
Lcom/secneo/apkwrapper/CP;
Lcom/secneo/apkwrapper/H$a;
Lcom/secneo/apkwrapper/H;
Lcom/secneo/apkwrapper/a;
Lcom/secneo/apkwrapper/b;
```

就这些。剩下的 103MB 是啥？

```python
b = open('classes.dex','rb').read()
pay = b[0x5400:]        # 壳数据到 0x5400 结束
print(len(pay)/1024/1024)   # 103.17 MB
```

Fer 一下熵：

```python
import math
from collections import Counter
c = Counter(pay)
H = -sum((n/len(pay))*math.log2(n/len(pay)) for n in c.values())
print(H)    # 4.446
```

4.4。加密数据这个长度怎么也得 7.9 以上。**没加密。**

再 grep 类描述符：

```python
import re
desc = re.findall(rb'L[A-Za-z0-9_/$]{3,80};', pay)
print(len(desc))    # 76593
print(desc[:5])
# Landroid/app/Activity;
# Landroid/app/Application;
# Lcom/bytedance/sdk/openadsdk/CSJSplashAd;
```

七万多个，全是明文。随便挑一个：

```bash
grep -a 'Lcom/xunlei/download/DownloadManager;' classes.dex
```

命中。业务类名一个不少。

**所以壳干了什么**：把 method 数据重排了，类名和字段名没动。运行时要靠 `libDexHelper.so` 在内存里还原成合法 dex。静态拿不到方法体，但结构全在。

---

## 3. 真正的业务在哪

`assets/index.android.bundle`。6MB。

看文件头：

```python
print(open('assets/index.android.bundle','rb').read(16).hex())
# c61fbc03c103191f60 000000 ...
```

前 8 字节 `c6 1f bc 03 c1 03 19 1f` → 反过来就是 `0x1f1903c103bc1fc6`。

这是 Hermes 字节码的 magic。版本号在 offset 8。

```bash
pip install hermes-dec
hbc-file-parser assets/index.android.bundle
```

```
| Magic          | 0x1f1903c103bc1fc6 |
| Version        | 96                 |
| FunctionCount  | 29456              |
| StringCount    | 42530              |
```

HBC v96。`hbctool` 只支持到 76 左右，会直接 assert 失败。用 `hermes-dec`。

```bash
hbc-decompiler assets/index.android.bundle out.js
```

出 40MB 伪代码。长这样：

```
r1 = function(a0) { // Original name: AdModal, environment: r2
    _fun27969: for(var _fun27969_ip = 0; ; ) switch(_fun27969_ip) {
case 0:
    r0 = a0;
    r5 = r0.visible;
    r25 = r0.seconds;
    r16 = r0.finished;
    ...
```

函数名被 Metro 搅过，但 `Original name` 注释里保留了原名。这是救命的东西。

---

## 4. 检索技巧

**搜中文字符串，不要搜函数名。**

```python
import re
t = open('out.js', encoding='utf-8', errors='replace').read()

for m in re.finditer('秒后完成', t):
    print(t[m.start()-14000 : m.start()+4000])
```

一下就定位到那个假的广告倒计时组件。里面的文案：

```
'广告任务'
'时长加速中'
'观看完成即可领取奖励'
'坚持一下，奖励马上到账'
'太棒了！本次奖励已到账'
```

顺着就能找到 `AdModal`。

不过这组件最后发现是**死代码**——全工程没有任何地方实例化它。属于开发留的半成品。真正跑的是 `showRewardVideoAD` 那条链路。

**找接口**

```python
paths = re.findall(r"'/(?:xlppc|api)[a-zA-Z0-9_/\-]+'", t)
for p in sorted(set(paths)):
    print(p)
```

出 40 多条。`mobileGLTaskList`、`mobileTaskSpark/claim`、`mobileGLTaskPush` 都在里面。

**找密钥**

签名函数附近会有一个从模块尾传上来的常量：

```python
re.finditer(r"r1 = '([0-9a-f]{16,40})';\s*\n\s*var _closure1_slot\d+ = r1;", t)
```

这个样本里能提出来一个 19 位的十六进制串（此处不转录，按上面的正则自己跑一遍就出来了）。

算法本身：

```
Object.keys(params).sort()
  .map((k, i) => (i ? '&' : '') + k + '=' + params[k])
  .join('')
→ MD5 → toLowerCase
```

注意它把 `key` 自己也当参数拼进去了。所以最终字符串里会出现 `key=<那个常量>`。

---

## 5. 自校验与加密：自己看

这块不写结论。给几个观察点和判断思路，剩下的自己跑。

**起点**。两样东西值得先看：

```
assets/meta-data/manifest.mf     60KB，无换行，base64 字符集
lib/<abi>/libDexHelper.so        壳本体
```

`manifest.mf` 那 60KB 连一个换行都没有，字符集分布又很均匀。`libDexHelper.so` 里是壳的全部逻辑。

**熵**。加密数据通常在 7.9 以上，压缩在 7 左右，重排过的裸数据在 4 上下。

```python
import math
from collections import Counter
c = Counter(data)
H = -sum((n/len(data))*math.log2(n/len(data)) for n in c.values())
print(H)
```

先测 `manifest.mf`，再测 `classes.dex` 从 `0x5400` 到文件尾那一大段。两段结果不会一样，差异本身就是线索。

**分段**。`manifest.mf` 里 `=` 只出现 6 次，位置很规律。按它切开看每段长度，长度本身就说明结构。

**明文残留**。直接在二进制里 grep 类描述符：

```python
import re
re.findall(rb'L[A-Za-z0-9_/$]{3,80};', data)
```

命中上到万，说明这段没加密，只是结构被改过。

**行为层面**。静态只看出"有没有"，看不出"什么时候检查"。两个方向：

一是启动时校验还是持续校验 —— 观察 App 从点图标到能操作这段过程里壳的 native 层加载了什么。只扫一遍的话，动态介入的窗口就大。

二是扫什么 —— 壳 `.so` 的字符串表能反映它在意的点。常见方向：进程自身映射表、调试器连接状态、常见 hook 框架库名、root 相关路径。这些串在不在、有几条，比结论更有信息量。

```python
import re
ss = re.findall(rb'[\x20-\x7e]{5,}', open('libDexHelper.so','rb').read())
for k in (b'/proc/self/maps', b'ptrace', b'frida', b'xposed', b'/system/bin/su'):
    hits = [s for s in ss if k in s]
    print(k.decode(), len(hits))
```

数字自己读。

**一句提醒**。改 APK 里的文件，启动时的校验大概率会发现。想动就动内存，别落盘。这只是推测，拦不拦、拦到什么程度，自己跑一遍才有数。观测样本不足以支撑结论，不写死。

**顺带**。`manifest.mf` 那 60KB 里有一段熵到 7.9 以上。密钥大概在哪，想想 `libDexHelper.so` 里那几段大 `.rodata`。真要解是另一条路，跟本次主题（业务逻辑缺校验）是两回事。这次没往深里走，留给有兴趣的人。
