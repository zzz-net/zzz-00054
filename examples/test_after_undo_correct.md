# 事件时间线复盘报告

**批次名称**: 标注撤销功能测试
**批次 ID**: 20260615_011600
**描述**: 验证标注撤销功能完整流程
**创建时间**: 2026-06-15T01:16:00.621187
**规则版本**: 1.0.0
**导出时间**: 2026-06-15 01:17:37

## 📊 统计概览

- **总事件数**: 45
- **时间范围**: 2026-06-14T09:15:00 ~ 2026-06-15T01:16:12.043174
- **时间缺口数**: 3
- **阶段数**: 0

### 按状态分布

- ❓ 待确认: 45

### 按严重级别分布

- 🔥 CRITICAL: 6
- ❌ ERROR: 6
- ⚠️ WARNING: 8
- ℹ️ INFO: 24
- 🔍 DEBUG: 1

### 按来源分布

- 📁 应用日志: 20
- 📁 人工备注: 11
- 📁 告警: 14

## ⏱️ 时间缺口

超过阈值 (600s) 的时间间隔:

| # | 开始时间 | 结束时间 | 间隔 |
|---|---------|---------|------|
| 1 | 2026-06-14 09:16:45 | 2026-06-14 09:30:00 | 13m15s |
| 2 | 2026-06-14 09:32:45 | 2026-06-14 09:45:30 | 12m45s |
| 3 | 2026-06-14 10:35:00 | 2026-06-15 01:16:12 | 14h41m12s |

## 📋 事件详情

### 📅 2026-06-14

共 43 条事件

### 1. ℹ️ ❓ [INFO] Application started successfully, version=2.3.1

- **时间**: 2026-06-14 09:15:00.000
- **ID**: `a12e9f5e1b68cf28`
- **来源**: 日志 (`app.log`:1)
- **状态**: 待确认

**详细信息**:

> Application started successfully, version=2.3.1

---

### 2. ℹ️ ❓ [INFO] 日常流量低谷期，系统正常运行

- **时间**: 2026-06-14 09:15:00.000
- **ID**: `a548a1c34b2bca9c`
- **来源**: 备注 (`notes.json`:1)
- **状态**: 待确认

**详细信息**:

> 日常流量低谷期，系统正常运行

---

### 3. 🔍 ❓ [DEBUG] Loading configuration from /etc/app/config.yaml

- **时间**: 2026-06-14 09:15:30.000
- **ID**: `62bf677a96a633a0`
- **来源**: 日志 (`app.log`:2)
- **状态**: 待确认

**详细信息**:

> Loading configuration from /etc/app/config.yaml

---

### 4. ℹ️ ❓ [INFO] Database connection pool initialized with 20 connections

- **时间**: 2026-06-14 09:16:45.000
- **ID**: `fbe2be1aa4a76934`
- **来源**: 日志 (`app.log`:3)
- **状态**: 待确认

**详细信息**:

> Database connection pool initialized with 20 connections

---

### 5. ℹ️ ❓ [INFO] 运维人员：注意到内存使用缓慢上升，决定持续观察

- **时间**: 2026-06-14 09:30:00.000
- **ID**: `7b212b8c15eee719`
- **来源**: 备注 (`notes.json`:2)
- **状态**: 待确认

**详细信息**:

> 运维人员：注意到内存使用缓慢上升，决定持续观察

**扩展字段**:

- `author`: ops_zhang

---

### 6. ⚠️ ❓ [WARNING] High memory usage detected: 85%

- **时间**: 2026-06-14 09:30:12.000
- **ID**: `11304440b62afe9b`
- **来源**: 日志 (`app.log`:4)
- **状态**: 待确认

**详细信息**:

> High memory usage detected: 85%

---

### 7. ⚠️ ❓ [WARNING] Memory usage exceeds 80% threshold

- **时间**: 2026-06-14 09:30:15.000
- **ID**: `e8a960a9183dd894`
- **来源**: 告警 (`alerts.csv`:2)
- **状态**: 待确认

**详细信息**:

> Memory usage exceeds 80% threshold

**扩展字段**:

- `source`: monitoring
- `host`: web-01

---

### 8. ℹ️ ❓ [INFO] User authentication successful, user_id=10086

- **时间**: 2026-06-14 09:32:45.000
- **ID**: `470c829bc289a567`
- **来源**: 日志 (`app.log`:5)
- **状态**: 待确认

**详细信息**:

> User authentication successful, user_id=10086

---

### 9. ❌ ❓ [ERROR] Failed to connect to payment gateway, timeout after 30s

- **时间**: 2026-06-14 09:45:30.000
- **ID**: `b3cb2da3c2ae6e3b`
- **来源**: 日志 (`app.log`:6)
- **状态**: 待确认

**详细信息**:

> Failed to connect to payment gateway, timeout after 30s

**合并来源** (2 条):

- `app.log`:6
- `app.log`:8

---

### 10. ❌ ❓ [ERROR] Payment gateway timeout

- **时间**: 2026-06-14 09:45:30.000
- **ID**: `35b0acbda155aedf`
- **来源**: 告警 (`alerts.csv`:3)
- **状态**: 待确认

**详细信息**:

> Payment gateway timeout

**合并来源** (3 条):

- `alerts.csv`:3
- `alerts.csv`:4
- `alerts.csv`:6

**扩展字段**:

- `source`: payment-service
- `host`: pay-01

---

### 11. ❌ ❓ [ERROR] 支付网关开始报超时错误，已通知值班工程师排查

- **时间**: 2026-06-14 09:45:30.000
- **ID**: `e1c363964e2bd68a`
- **来源**: 备注 (`notes.json`:3)
- **状态**: 待确认

**详细信息**:

> 支付网关开始报超时错误，已通知值班工程师排查

**扩展字段**:

- `author`: on_call_li

---

### 12. ❌ ❓ [ERROR] Payment service returned 500 Internal Server Error for order #20260614001

- **时间**: 2026-06-14 09:45:31.000
- **ID**: `128d54dd4c4d6f86`
- **来源**: 日志 (`app.log`:7)
- **状态**: 待确认

**详细信息**:

> Payment service returned 500 Internal Server Error for order #20260614001

**合并来源** (2 条):

- `app.log`:7
- `app.log`:10

---

### 13. 🔥 ❓ [CRITICAL] 500 error rate > 5% for payment API

- **时间**: 2026-06-14 09:45:35.000
- **ID**: `02df1e4faa30aadc`
- **来源**: 告警 (`alerts.csv`:5)
- **状态**: 待确认

**详细信息**:

> 500 error rate > 5% for payment API

**扩展字段**:

- `source`: monitoring
- `host`: lb-01

---

### 14. ⚠️ ❓ [WARNING] Retrying payment for order #20260614001, attempt 2

- **时间**: 2026-06-14 09:45:40.000
- **ID**: `66e8ab15bfad443c`
- **来源**: 日志 (`app.log`:9)
- **状态**: 待确认

**详细信息**:

> Retrying payment for order #20260614001, attempt 2

---

### 15. ❌ ❓ [ERROR] Circuit breaker half-open state reached

- **时间**: 2026-06-14 09:45:42.000
- **ID**: `a27661f097afddda`
- **来源**: 告警 (`alerts.csv`:7)
- **状态**: 待确认

**详细信息**:

> Circuit breaker half-open state reached

**扩展字段**:

- `source`: resilience4j
- `host`: pay-01

---

### 16. 🔥 ❓ [CRITICAL] Payment processing completely unavailable, affecting 15 pending orders

- **时间**: 2026-06-14 09:46:00.000
- **ID**: `3188353c103be5f2`
- **来源**: 日志 (`app.log`:11)
- **状态**: 待确认

**详细信息**:

> Payment processing completely unavailable, affecting 15 pending orders

---

### 17. 🔥 ❓ [CRITICAL] Payment processing unavailable for 30s

- **时间**: 2026-06-14 09:46:00.000
- **ID**: `6e5d4bdb7d4c093d`
- **来源**: 告警 (`alerts.csv`:8)
- **状态**: 待确认

**详细信息**:

> Payment processing unavailable for 30s

**扩展字段**:

- `source`: sre-alerts
- `host`: mon-01

---

### 18. 🔥 ❓ [CRITICAL] 初步判断为上游第三方支付接口异常，非我方服务问题

- **时间**: 2026-06-14 09:46:00.000
- **ID**: `8b9c90251275077d`
- **来源**: 备注 (`notes.json`:4)
- **状态**: 待确认

**详细信息**:

> 初步判断为上游第三方支付接口异常，非我方服务问题

**扩展字段**:

- `时间`: 2026-06-14 09:46:00
- `备注`: 初步判断为上游第三方支付接口异常，非我方服务问题
- `owner`: 支付团队

---

### 19. 🔥 ❓ [CRITICAL] Circuit breaker OPEN for payment-service

- **时间**: 2026-06-14 09:46:05.000
- **ID**: `2f9654e712129420`
- **来源**: 告警 (`alerts.csv`:9)
- **状态**: 待确认

**详细信息**:

> Circuit breaker OPEN for payment-service

**扩展字段**:

- `source`: resilience4j
- `host`: pay-01

---

### 20. ℹ️ ❓ [INFO] Circuit breaker opened for payment-service

- **时间**: 2026-06-14 09:46:15.000
- **ID**: `85c87808bbebbff5`
- **来源**: 日志 (`app.log`:12)
- **状态**: 待确认

**详细信息**:

> Circuit breaker opened for payment-service

---

### 21. ⚠️ ❓ [WARNING] Database query latency increased: avg=850ms

- **时间**: 2026-06-14 09:50:00.000
- **ID**: `9bc1fdc2bf1a1ccb`
- **来源**: 日志 (`app.log`:13)
- **状态**: 待确认

**详细信息**:

> Database query latency increased: avg=850ms

---

### 22. ⚠️ ❓ [WARNING] Database query latency spike

- **时间**: 2026-06-14 09:50:00.000
- **ID**: `3bf9ff2ed822a682`
- **来源**: 告警 (`alerts.csv`:10)
- **状态**: 待确认

**详细信息**:

> Database query latency spike

**扩展字段**:

- `source`: mysql-monitor
- `host`: db-01

---

### 23. ⚠️ ❓ [WARNING] SRE 介入，开始准备回滚预案

- **时间**: 2026-06-14 09:50:00.000
- **ID**: `fb43444793a701d2`
- **来源**: 备注 (`notes.json`:5)
- **状态**: 待确认

**详细信息**:

> SRE 介入，开始准备回滚预案

**扩展字段**:

- `owner`: SRE

---

### 24. ❌ ❓ [ERROR] Pending orders queue size > 10

- **时间**: 2026-06-14 09:55:00.000
- **ID**: `97120286b8b8654a`
- **来源**: 告警 (`alerts.csv`:11)
- **状态**: 待确认

**详细信息**:

> Pending orders queue size > 10

**扩展字段**:

- `source`: monitoring
- `host`: pay-01

---

### 25. ℹ️ ❓ [INFO] SRE team notified via PagerDuty

- **时间**: 2026-06-14 09:55:30.000
- **ID**: `2f6719a79f425a69`
- **来源**: 日志 (`app.log`:14)
- **状态**: 待确认

**详细信息**:

> SRE team notified via PagerDuty

---

### 26. ℹ️ ❓ [INFO] 与第三方支付确认：他们新版本发布了有问题的变更，正在回滚

- **时间**: 2026-06-14 10:00:00.000
- **ID**: `7421510accabe2ab`
- **来源**: 备注 (`notes.json`:6)
- **状态**: 待确认

**详细信息**:

> 与第三方支付确认：他们新版本发布了有问题的变更，正在回滚

**扩展字段**:

- `source`: 外部沟通

---

### 27. ℹ️ ❓ [INFO] Rolling back payment service to version 2.3.0

- **时间**: 2026-06-14 10:05:00.000
- **ID**: `7988fd1d316ec05b`
- **来源**: 日志 (`app.log`:15)
- **状态**: 待确认

**详细信息**:

> Rolling back payment service to version 2.3.0

---

### 28. ℹ️ ❓ [INFO] Deployment started: payment-service v2.3.0

- **时间**: 2026-06-14 10:05:00.000
- **ID**: `724b1e910cb1dde9`
- **来源**: 告警 (`alerts.csv`:12)
- **状态**: 待确认

**详细信息**:

> Deployment started: payment-service v2.3.0

**扩展字段**:

- `source`: ci-cd
- `host`: deploy-01

---

### 29. ℹ️ ❓ [INFO] 决定回滚我方支付服务到 v2.3.0 作为预防措施

- **时间**: 2026-06-14 10:05:00.000
- **ID**: `9cdcaa3e282afcfa`
- **来源**: 备注 (`notes.json`:7)
- **状态**: 待确认

**详细信息**:

> 决定回滚我方支付服务到 v2.3.0 作为预防措施

**扩展字段**:

- `action`: rollback

---

### 30. ℹ️ ❓ [INFO] Health check passing for payment-service v2.3.0

- **时间**: 2026-06-14 10:08:00.000
- **ID**: `64b224baa3c56826`
- **来源**: 告警 (`alerts.csv`:13)
- **状态**: 待确认

**详细信息**:

> Health check passing for payment-service v2.3.0

**扩展字段**:

- `source`: monitoring
- `host`: pay-01

---

### 31. ℹ️ ❓ [INFO] Payment service v2.3.0 deployment started

- **时间**: 2026-06-14 10:08:45.000
- **ID**: `88fa2abf66a0a771`
- **来源**: 日志 (`app.log`:16)
- **状态**: 待确认

**详细信息**:

> Payment service v2.3.0 deployment started

---

### 32. ℹ️ ❓ [INFO] Payment service v2.3.0 is now healthy

- **时间**: 2026-06-14 10:12:30.000
- **ID**: `2206e40cc74f2977`
- **来源**: 日志 (`app.log`:17)
- **状态**: 待确认

**详细信息**:

> Payment service v2.3.0 is now healthy

---

### 33. ℹ️ ❓ [INFO] Circuit breaker CLOSED for payment-service

- **时间**: 2026-06-14 10:12:30.000
- **ID**: `ab9cf98a3668e3f3`
- **来源**: 告警 (`alerts.csv`:14)
- **状态**: 待确认

**详细信息**:

> Circuit breaker CLOSED for payment-service

**合并来源** (2 条):

- `alerts.csv`:14
- `app.log`:18

**扩展字段**:

- `source`: resilience4j
- `host`: pay-01

---

### 34. ℹ️ ❓ [INFO] Payment processing restored, retrying pending orders

- **时间**: 2026-06-14 10:15:00.000
- **ID**: `4a85dc8d421508c7`
- **来源**: 日志 (`app.log`:19)
- **状态**: 待确认

**详细信息**:

> Payment processing restored, retrying pending orders

---

### 35. ⚠️ ❓ [WARNING] Pending orders queue size back to normal

- **时间**: 2026-06-14 10:15:00.000
- **ID**: `fdb5602cafeabd2a`
- **来源**: 告警 (`alerts.csv`:15)
- **状态**: 待确认

**详细信息**:

> Pending orders queue size back to normal

**扩展字段**:

- `source`: monitoring
- `host`: pay-01

---

### 36. ℹ️ ❓ [INFO] 系统恢复正常，订单队列开始消费

- **时间**: 2026-06-14 10:15:00.000
- **ID**: `30c9c4ee92ae7cb2`
- **来源**: 备注 (`notes.json`:8)
- **状态**: 待确认

**详细信息**:

> 系统恢复正常，订单队列开始消费

---

### 37. ⚠️ ❓ [WARNING] System recovery verified by SRE

- **时间**: 2026-06-14 10:20:00.000
- **ID**: `9dc06bc0b694d758`
- **来源**: 告警 (`alerts.csv`:16)
- **状态**: 待确认

**详细信息**:

> System recovery verified by SRE

**扩展字段**:

- `source`: sre-reports
- `host`: mon-01

---

### 38. ℹ️ ❓ [INFO] All pending orders processed successfully

- **时间**: 2026-06-14 10:20:45.000
- **ID**: `c7a3678347b4a996`
- **来源**: 日志 (`app.log`:20)
- **状态**: 待确认

**详细信息**:

> All pending orders processed successfully

---

### 39. ℹ️ ❓ [INFO] System metrics back to normal levels

- **时间**: 2026-06-14 10:25:00.000
- **ID**: `7f49bfccfd745d92`
- **来源**: 日志 (`app.log`:21)
- **状态**: 待确认

**详细信息**:

> System metrics back to normal levels

---

### 40. ℹ️ ❓ [INFO] All metrics within normal range

- **时间**: 2026-06-14 10:25:00.000
- **ID**: `45155a5e63d5ef7d`
- **来源**: 告警 (`alerts.csv`:18)
- **状态**: 待确认

**详细信息**:

> All metrics within normal range

**扩展字段**:

- `source`: monitoring
- `host`: mon-01

---

### 41. ℹ️ ❓ [INFO] Post-incident cleanup completed

- **时间**: 2026-06-14 10:30:00.000
- **ID**: `346d707cd3832338`
- **来源**: 日志 (`app.log`:23)
- **状态**: 待确认

**详细信息**:

> Post-incident cleanup completed

---

### 42. ℹ️ ❓ [INFO] 故障复盘会议预定明天上午10点

- **时间**: 2026-06-14 10:30:00.000
- **ID**: `3a11151707ff0f12`
- **来源**: 备注 (`notes.json`:9)
- **状态**: 待确认

**详细信息**:

> 故障复盘会议预定明天上午10点

---

### 43. ℹ️ ❓ [INFO] Final verification check passed

- **时间**: 2026-06-14 10:35:00.000
- **ID**: `ed8c9165518fbe9d`
- **来源**: 日志 (`app.log`:25)
- **状态**: 待确认

**详细信息**:

> Final verification check passed

---

### 📅 2026-06-15

共 2 条事件

### 1. ℹ️ ❓ [INFO] 这条备注没有时间戳，应该使用当前时间

- **时间**: 2026-06-15 01:16:12.043
- **ID**: `8481b7cfba19c23f`
- **来源**: 备注 (`notes.json`:10)
- **状态**: 待确认

**详细信息**:

> 这条备注没有时间戳，应该使用当前时间

---

### 2. 🔥 ❓ [CRITICAL] 根因确认：第三方支付新版本接口协议变更未同步

- **时间**: 2026-06-15 01:16:12.043
- **ID**: `762c20b625b5543a`
- **来源**: 备注 (`notes.json`:11)
- **状态**: 待确认

**详细信息**:

> 根因确认：第三方支付新版本接口协议变更未同步

**扩展字段**:

- `bad-timestamp`: 2026/06/14 10:35:00

---

## ⚙️ 规则配置

- **规则版本**: 1.0.0
- **去重时间窗口**: 300s
- **缺口阈值**: 600s
- **去重相似度阈值**: 0.8
