# glacc-audit

给梨加速器（`com.geilijiasu.glacc`，v1.26.10.1）任务中心业务逻辑审计。

一句话：**领奖环节的前置状态没有被校验。** 请求本身合法就发奖。

---

## 结论

服务端在发「看广告得时长」奖励时，只校验请求参数本身合法，不校验广告是否真的播放过。

`mobileTaskSpark/claim` 的 `action=subtask_return` 分支，缺了前置状态校验。

---

## 正常链路 vs 实际

设计上是这样：

```
1. GET  mobileGLTaskList        → 拿 snapshotId / strategyId / taskId
2. 用户点击「领时长」
3. 调起广告 SDK 播激励视频（advertLink 是广告位 ID）
4. SDK 回调「播放完成」
5. POST mobileGLTaskPush        上报完成事件（timestamp + extData + sign）
6. POST mobileTaskSpark/claim   领奖
7. 服务端核对第 5 步 → 发奖
```

实际：

```
1. GET  mobileGLTaskList
6. POST mobileTaskSpark/claim   ← 直接跳到这里
7. 服务端返回 code:0 → 发奖
```

实际观测到：省去中间环节后，接口依然返回 `rewardStatus: success`。

---

## 关键请求

**拉列表**

```
GET /xlppc.gacs/api/gxsdn/act/advert/mobileGLTaskList?appid=8
```

响应顶层有两个东西必须拿：`data.snapshotId`（UUID）、`data.strategyId`（整数）。
子任务在 `data.list[].taskDetailList[]` 里，取 `masterTaskId` 和 `taskId`。

**领奖**

```
POST /xlppc.gacs/api/gxsdn/act/advert/mobileTaskSpark/claim
```

```json
{
  "action": "subtask_return",
  "masterTaskId": 52,
  "taskId": 222,
  "requestId": "1792984791513",
  "snapshotId": "<上一步拿的>",
  "strategyId": 23
}
```

返回：

```json
{
  "code": 0,
  "result": "ok",
  "data": {
    "orderId": "mt:spark:bd3e432ea9d08301a94640e74db44ac87f0bf20f",
    "rewardStatus": "success",
    "rewardMinutes": 1,
    "rewardCoin": 80,
    "alreadyClaimed": false,
    "rewardedAt": 1792984791
  }
}
```

**核对入账**

```
GET /xlppc.gacs/api/gxsdn/gold/get_user_wallet
```

这个接口是独立的资产查询，不经过 claim，拿它对比前后数值才算真凭据。

---

## 实测

同一账号，11 个带 `advertLink` 的子任务：

| | 前 | 后 |
|---|---|---|
| `score` 积分 | 319600 | 320480 |
| `remain_acc_second` | 239700 | 240360 |

全部 `code: 0`，全程零广告。

---

## 服务端行为观察

几个细节，对定位有用：

**`snapshotId` 是一次性的。** 领完一次，用同一个快照再请求 → `code: 10053 请求无效`。
说明防重放做了，但只做到快照层。

**不同 `action` 走不同分支。** 试了一圈：

| action | code |
|---|---|
| `subtask_return` | 0 |
| `master_expand` | 10053 |
| `subtask_finish` / `task_finish` / `claim` / `finish` | 10000 |

服务端对 action 是有分支处理的，不是一把梭。问题就集中在那一个分支上。

**`advertLink` 为空的子任务领不了。** 比如「愉悦赚任务」下的几个，直接打回来 `10053`。
这类另有判定，没被这个问题覆盖。

---

## 定位建议

修复点在 `mobileTaskSpark/claim` 的 `subtask_return` 分支：

1. 发奖前查广告平台回传（按 device_id + advertLink + 时间窗），查不到拒掉
2. 或者改成一次性票据：SDK 回调后服务端签 ticket，claim 必须消费，用完即焚
3. 补时间间隔下限，激励视频至少 15–30 秒
4. 同设备高频领奖加风控

另外顺手提一句：`adStats` 用的签名密钥是明文的，躺在 `index.android.bundle` 里，`hermes-dec` 跑一遍就能看见。现在是以 `r1 = '<19位十六进制>'; var _closure1_slot8 = r1;` 这种形式写死的。密钥放客户端等于没放。

---

## 怎么找到的

给后来人留个路标。

**壳是唬人的。** SecNeo 梆梆企业版，`classes.dex` 尾部塞了 100MB 载荷，`manifest.mf` 是加密的完整性表，`libDexHelper.so` 一堆。看着很硬。

但：

- 载荷熵 4.4，**没加密**，只是结构重排
- 7 万多个类描述符全是明文，`grep 'Lcom/xunlei/download/DownloadManager;'` 直接命中
- 真正的业务在 `assets/index.android.bundle` —— 标准 Hermes 字节码，明文

`hermes-dec` 跑一遍，40MB 可读伪代码。业务逻辑一行不少。

**壳保护的是代码怎么写的，保护不了业务怎么跑的。**

**检索靠中文字符串。** RN 的函数名被 Metro 搅过，但 UI 文案不搅。直接搜 `'秒后完成'`、`'广告任务'`、`'广告获取失败，请稍后重试'`，比搜函数名准得多。

**抓包别设全局代理。** App 内 WebView 有自己的信任链，全局代理会让它报「无法访问页面」。用 iptables 只劫持业务域名那段 IP：

```bash
iptables -t nat -A OUTPUT -p tcp -d <业务IP段>/24 --dport 443 -j REDIRECT --to-ports 8080
```

广告 SDK 和 WebView 走直连，互不干扰。

---

## 仓库里有什么

```
verify/claim.py       领奖请求验证脚本，凭证位留空
docs/analysis.md      静态分析路径：壳怎么绕、bundle 怎么还原、密钥在哪
docs/traffic.md       动态抓包：环境配置、iptables 规则、坑
README.md             这份
```

脚本里的凭证位留空。

---

## 说明

已提交官方。

本项目仅作为参考学习、巩固自身知识。

---

## 观测范围

自有账号，未批量执行，未触碰他人数据，未做压力测试。