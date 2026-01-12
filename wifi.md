# 8155 车机平台 Wi-Fi 测试与调试手册 (ADB 版)

**文档标题：** 8155 车机平台 Wi-Fi 测试与调试手册 (ADB 版) - 完整版

**作者：** [您的姓名或专业车载测试工程师]

**修订日期：** 2026 年 1 月 8 日

**版本：** v2.0 (基于原 v1.1 优化与补全：添加性能测试、安全性评估、稳定性测试、电源管理、错误排查、工具准备等遗漏内容；优化结构、添加示例输出与最佳实践；扩展 DBS 测试场景；统一指令格式并添加注意事项)

**文档目的：** 本手册旨在为测试人员提供通过 ADB 指令对 8155 车机系统的 Wi-Fi STA（客户端）与 AP（热点）模式进行全面的性能评估、故障排查、状态监控、安全性验证和优化指导。作为专业车载测试工程师，我对原手册进行了补全与优化。原手册遗漏的主要知识点包括：性能指标量化测试（如吞吐量、延迟）、安全性评估（加密与漏洞检查）、连接稳定性与漫游测试、电源管理影响、常见错误代码排查、额外工具准备（如 iperf 安装与使用）、Wi-Fi 驱动版本检查、内核日志分析、多设备兼容性测试等。这些遗漏可能导致测试不全面，尤其在车载环境下（如移动信号干扰、高温等）。本版已全面补全，形成一份完整的大全。

---

## 目录

1. 引言与前提准备
2. 网络架构快速识别
3. STA (客户端) 模式测试
4. AP (热点) 模式测试
5. DBS (双频并发) 测试
6. 性能测试与量化评估
7. 安全性评估
8. 连接稳定性与漫游测试
9. 电源管理与功耗测试
10. 日志抓取与深度分析
11. 常见错误排查
12. 常用维护指令补充
13. 附录：工具准备与最佳实践

---

## 1. 引言与前提准备

### 1.1 概述

8155 平台（基于高通骁龙 8155 芯片）支持先进的 Wi-Fi 功能，包括 STA 模式（连接外部热点）、AP 模式（车机作为热点）、DBS（双频并发，支持同时 STA+AP）。本手册聚焦 ADB（Android Debug Bridge）指令测试，适用于车载环境下的 Wi-Fi 调试。测试前确保：

- 设备已 root 或有 ADB 调试权限。
- ADB 已连接：`adb devices` 显示设备在线。
- Wi-Fi 模块已启用，避免硬件故障（如天线松动）。

### 1.2 遗漏补全：工具准备

原手册未提及额外工具安装，这些工具对性能测试至关重要：

- **iperf 工具**：用于吞吐量测试。安装指令（假设设备支持）：
  ```
  adb push iperf /data/local/tmp/
  adb shell chmod +x /data/local/tmp/iperf
  ```
  - 下载 iperf 二进制文件（从 PC 端推送）。
- **Wi-Fi 驱动版本检查**：确认固件版本以排查兼容性问题。
  ```
  adb shell "getprop ro.vendor.wifi.sap.version"  # 或类似属性
  adb shell "dmesg | grep -i wifi"  # 查看内核加载的Wi-Fi模块
  ```
- **其他工具**：tcpdump（抓包）、netperf（网络性能）等，可通过 ADB 推送安装。

### 1.3 最佳实践

- 所有指令前添加`adb shell`以进入设备 shell。
- 测试环境：模拟车载场景（如移动中信号干扰、使用屏蔽箱控制信号强度）。
- 注意安全：测试中避免泄露敏感数据（如 SSID 密码）。

---

## 2. 网络架构快速识别

在 8155 平台上，通常采用虚拟多网卡架构。首先需确认接口分配情况与系统服务状态。

### 2.1 综合状态一键查询（最推荐）

此指令可快速查看 Wi-Fi 整体开关状态及当前角色（Client/SoftAp）。

```
adb shell cmd wifi status
```

**示例输出关键点：**

- Wifi is enabled: 表示 STA 模式开启。
- Wifi AP is enabled: 表示 AP 模式开启。
- Scanning is enabled: 表示后台扫描开启。
- **优化添加：** 如果输出显示"Wifi is disabled"，检查硬件开关或重启 Wi-Fi 服务。

### 2.2 接口枚举命令

```
adb shell "ip addr show | grep -E 'wlan|p2p|softap'"
```

- wlan0: 默认 STA 接口（连接外部 Wi-Fi）。
- wlan1 / softap0: 默认 AP 接口（车机发射热点）。
- p2p0: Wi-Fi 直连接口（用于手机互联）。
- **优化添加：** 检查 IP 地址分配（e.g., 192.168.x.x for AP），确认无冲突。

### 2.3 遗漏补全：多网卡冲突检查

使用`adb shell ip link show`查看所有接口状态，避免 DBS 模式下接口重叠。

---

## 3. STA (客户端) 模式测试

### 3.1 详细连接状态查看

```
adb shell "wpa_cli -i wlan0 status"
```

**关键观察项（优化添加示例）：**

- ssid: 确认连接的目标（e.g., "MyHomeWiFi"）。
- freq: 频率（5G: ~5000MHz, 2.4G: ~2400MHz）。
- wpa_state: 必须为 COMPLETED（若为 SCANNING，表示连接失败）。
- ip_address: 检查 DHCP 分配的 IP。

### 3.2 信号强度实时监控 (RSSI)

```
adb shell "wpa_cli -i wlan0 signal_poll"
```

- RSSI: 建议范围 -30dBm 至 -65dBm（<-80dBm 表示弱信号，可能丢包）。
- LINKSPEED: 当前空口协商速率（e.g., 866Mbps for 802.11ac）。
- **优化添加：** 循环监控：`while true; do adb shell wpa_cli -i wlan0 signal_poll; sleep 5; done`。

### 3.3 扫描周边热点

```
adb shell "wpa_cli -i wlan0 scan"
adb shell "wpa_cli -i wlan0 scan_results"
```

- **优化添加：** 过滤特定热点：`adb shell "wpa_cli -i wlan0 scan_results | grep SSID"`。

### 3.4 遗漏补全：连接特定热点

手动连接（测试自定义配置）：

```
adb shell "wpa_cli -i wlan0 add_network"
adb shell "wpa_cli -i wlan0 set_network 0 ssid '\"YourSSID\"'"
adb shell "wpa_cli -i wlan0 set_network 0 psk '\"YourPassword\"'"
adb shell "wpa_cli -i wlan0 enable_network 0"
```

---

## 4. AP (热点) 模式测试

### 4.1 运行状态与配置审计

```
adb shell "dumpsys wifi | grep -i SoftAp"
```

- **优化添加：** 检查 channel、bandwidth（e.g., 20/40/80MHz）。

### 4.2 快捷查询与控制（cmd wifi 系列）

- 查看 AP 状态：`adb shell cmd wifi status | grep -i AP`
- 开启热点：`adb shell cmd wifi set-wifi-ap-enabled enabled`
- 关闭热点：`adb shell cmd wifi set-wifi-ap-enabled disabled`
- **优化添加：** 设置热点名称/密码：使用 Android Settings API 或修改`/data/misc/wifi/softap.conf`（需 root）。

### 4.3 查看已连接终端 (Client) 详情

```
adb shell "ip neighbor show dev wlan1"
# 或查看ARP缓存表
adb shell "cat /proc/net/arp | grep wlan1"
```

- **优化添加：** 监控客户端 RSSI：`adb shell "hostapd_cli -i wlan1 all_sta"`（若 hostapd 可用）。

### 4.4 遗漏补全：热点配置修改

自定义热点：

```
adb shell settings put global tether_dun_required 0  # 禁用DUN检查
adb shell cmd wifi set-softap-configuration "SSID" "password" WPA2_PSK  # 示例，视系统支持
```

---

## 5. DBS (双频并发) 测试

8155 支持一边连 Wi-Fi (STA)，一边开热点 (AP)。

### 5.1 并发信道冲突检查

对比`wpa_cli -i wlan0 status`的 freq 与`dumpsys wifi`中 SoftAp 的 frequency。

- 测试场景：若两者都在 5GHz 且信道相同，需重点测试高负载下的丢包率。
- **优化添加：** 强制信道：修改 AP 配置避免冲突（e.g., STA on 2.4G, AP on 5G）。

### 5.2 遗漏补全：DBS 性能影响测试

- 检查交叉干扰：使用 iperf 测试 STA/AP 同时运行时的吞吐量下降。
- 场景扩展：移动环境中 DBS 稳定性（e.g., 车速>60km/h 时信号切换）。

---

## 6. 性能测试与量化评估（遗漏补全）

原手册未覆盖量化指标，这是车载 Wi-Fi 测试的核心。

### 6.1 吞吐量测试（使用 iperf）

- 服务器端（车机 AP 模式）：`adb shell /data/local/tmp/iperf -s -p 5001`
- 客户端（外部设备）：`iperf -c <车机IP> -p 5001 -t 60`
- STA 模式反之。目标：>100Mbps 无丢包。

### 6.2 延迟与丢包测试

```
adb shell ping -c 100 <gateway IP>  # 延迟<50ms，丢包<1%
```

### 6.3 带宽利用率

使用`adb shell "iw dev wlan0 link"`查看当前速率。

---

## 7. 安全性评估（遗漏补全）

### 7.1 加密检查

- STA：`adb shell wpa_cli -i wlan0 status | grep pairwise_cipher`（应为 CCMP/AES）。
- AP：`adb shell dumpsys wifi | grep -i security`（确保 WPA3 支持）。

### 7.2 漏洞扫描

- 检查 WPS 启用：`adb shell wpa_cli -i wlan0 wps_check_pin`（禁用以防攻击）。
- 测试：模拟 KRACK 攻击（需外部工具）。

---

## 8. 连接稳定性与漫游测试（遗漏补全）

### 8.1 断连重连测试

- 强制断连：`adb shell wpa_cli -i wlan0 disconnect`
- 重连：`adb shell wpa_cli -i wlan0 reconnect`
- 监控时间：目标<5s。

### 8.2 漫游测试

模拟信号切换：使用屏蔽箱渐变 RSSI，检查无缝切换（`adb logcat | grep roam`）。

---

## 9. 电源管理与功耗测试（遗漏补全）

### 9.1 Wi-Fi 电源模式检查

```
adb shell "dumpsys power | grep -i wifi"
```

- 测试场景：低功耗模式下信号稳定性（e.g., 车机休眠时 Wi-Fi 保持）。

### 9.2 功耗量化

使用`adb shell dumpsys batterystats | grep wifi`查看电流消耗（目标<50mA idle）。

---

## 10. 日志抓取与深度分析

### 10.1 实时日志过滤

```
adb logcat -v time | grep -iE "Wifi|wpa_supplicant|hostapd|WifiConfigStore"
```

### 10.2 获取完整 Wi-Fi 堆栈快照

```
adb shell "dumpsys wifi" > wifi_full_dump.txt
```

### 10.3 遗漏补全：内核日志

```
adb shell dmesg | grep -i wifi  # 检查驱动错误
```

---

## 11. 常见错误排查（遗漏补全）

| 错误描述           | 可能原因  | 排查指令                   |
| ------------------ | --------- | -------------------------- |
| wpa_state=INACTIVE | 密码错误  | wpa_cli status; 检查日志   |
| RSSI 波动大        | 干扰      | signal_poll; 切换信道      |
| AP 不广播          | 配置问题  | dumpsys wifi; 重启 AP      |
| DBS 冲突           | 信道重叠  | 对比 freq; 修改 AP channel |
| 连接超时           | DHCP 失败 | ip addr show; 检查 ARP     |

---

## 12. 常用维护指令补充

| 功能                         | 指令                                                         |
| ---------------------------- | ------------------------------------------------------------ | ---------- |
| 重置 Wi-Fi 配置              | adb shell cmd wifi forget-all-networks                       |
| 开启/关闭 Wi-Fi              | adb shell svc wifi enable / disable                          |
| 强制触发扫描                 | adb shell cmd wifi start-scan                                |
| **新增：** 重启 Wi-Fi 服务   | adb shell am restart wifi                                    |
| **新增：** 设置静态 IP (STA) | adb shell ifconfig wlan0 192.168.1.100 netmask 255.255.255.0 |
| **新增：** 检查 Wi-Fi MAC    | adb shell ip link show wlan0                                 | grep ether |

---

## 13. 附录：工具准备与最佳实践

- **工具列表：** iperf, tcpdump, netstat。
- **最佳实践：** 自动化脚本化测试（e.g., Python+ADB）；记录环境变量（如温度、车速）；定期更新固件。
- **参考资源：** 高通文档、Android Wi-Fi HAL 源代码。

**文档结束。** 此版本已全面优化与补全，确保覆盖车载 Wi-Fi 测试所有方面。如需进一步调整，请提供反馈。
