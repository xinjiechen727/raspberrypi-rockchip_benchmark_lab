# HEVC 实时多路解码测试工具 v1

这是可审计的 Linux Bash / Python GI 测试工具，面向 RK3588 `mppvideodec` 与 Raspberry Pi 5 `v4l2slh265dec`。**没有附带任何测量结果，也没有经过目标板实机验收。** 当前交付验证包含 Python 语法和独立计数逻辑测试；正式使用前必须完成下述上板验证。它是工程测试起点，不是经过认证的工业产品。

## 1. 测量口径

|字段|含义|
|---|---|
|planned|有限测试计划帧数：时长 × 每路帧率；源文件循环不会修改这个数|
|due|按统一单调时钟，目前应该已经释放的计划帧数|
|input|经过 parser、实际到达硬件解码器 sink pad 的有效唯一帧 ID 数|
|decoded|硬件解码器 src pad 输出的有效唯一帧 ID 数|
|delivered|Python 从 appsink 拉取到的有效唯一帧 ID 数，即本测试的实际下游交付|
|raw_*_buffers|各边界观察到的原始 buffer 数；重复或无效 PTS 不会隐藏在唯一计数中|
|late|实际接收时间 > 计划释放时间 + deadline 的帧数；迟到的帧仍计入 delivered|
|on_time|delivered − late；early/重复等异常会另外导致失败|
|input_missing|planned − input；运行时含尚未到期的帧，结束后才可解释|
|decode_missing|解码器已接收的 ID 中，尚未看到解码输出的数量|
|delivery_missing|已有解码输出但尚未被 Python 接收的 ID 数|
|backlog|已到达解码器入口但尚未交付的 ID 数，含解码内部/下游在途帧|
|inq / outq|两个显式 GStreamer queue 的当前 buffer 数；不等于整个系统积压|
|schedule_debt|已经到期但尚未到达解码入口的帧数，揭示上游反压|
|release_late|输入生产线程尝试 push 时超过 release-ms 容差的次数|
|missing_planned_delivery|测试结束时计划交付数 − 唯一有效交付数；另附缺失 ID 列表|

时间戳映射使用 `PTS = round(frame_id × 1e9 / fps)`，入口、出口、接收三个阶段均检查范围、偏差（容差 1 微秒）、重复和单调性。PTS 不可用、被插件改写或不符合契约时，判失败，不能继续声称零丢帧。源限制为无 B 帧，因此三个阶段都应按帧序输出。原始 buffer 总数与有效唯一计数同时保留。

**PASS** 要求正常 EOS、所有路 planned/input/decoded/delivered 完全一致、ID 集合一致、零迟到、零输入调度超限、零时间戳/重复异常。短测试的 PASS 仅代表短测试；`qualified_30min` 只有至少 1800 秒且 PASS 才为 true。三轮必须分别合格，不以平均值掩盖一次失败。

截止时间默认 **100 ms**，输入释放容差默认 **10 ms**，都是可调整的测试契约，不是官方硬件承诺。比较时必须使用相同参数，并在拍摄报告中公开。该延迟从计划压缩帧释放时间算起，包含输入积压、解码、排队和 Python 调度；不包含摄像头采集/编码/网络延迟。

程序明确设置 appsink `sync=false`、`drop=false`、有限缓冲。Python 主动持续拉帧；不让 BaseSink 时钟同步或默认丢帧机制代替计数。阻塞和追赶不会被当成成功：积压、调度债务、迟到及最终缺失分别暴露。所有样本都会被尝试交付，包括迟到帧。超时剩余帧属于未完成/缺失，不能武断归因于某个插件“主动丢帧”。

交付只表示拿到了原始视频 buffer 引用。未复制/读取全部像素，未显示、缩放、颜色转换或 AI 推理，不能据此宣称完整工业应用零丢帧。实际应用需将计数/截止检查移至真实消费端并重新验收。

## 2. 依赖与板端环境

Debian / Ubuntu / Raspberry Pi OS 系统依赖（管理员执行）：

```bash
sudo apt update
sudo apt install python3 python3-gi python3-gst-1.0 gir1.2-gstreamer-1.0 \
  gir1.2-gst-plugins-base-1.0 gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad ffmpeg util-linux
```

RK3588 需要板厂镜像提供的 Rockchip MPP 库和 GStreamer 插件；上述通用包并不保证安装 `mppvideodec`，不要为此盲目混装不同发行版的二进制。Pi 5 需要内核、设备访问权限和发行版构建都支持 stateless V4L2 HEVC。程序只接受明确指定的这两个硬解元素，不会自动降级软解，也不按 `/dev/video19` 编号猜平台。

```bash
gst-inspect-1.0 mppvideodec       # RK3588
gst-inspect-1.0 v4l2slh265dec     # Pi 5
/usr/bin/python3 -c 'import gi; gi.require_version("GstApp","1.0"); from gi.repository import Gst,GstApp; Gst.init(None); print(Gst.version_string())'
dmesg --color=never >/dev/null
```

若普通用户无法读取 dmesg，本工具**拒绝测试**。请由管理员按组织安全策略授予合适的日志读取权限；程序不会修改系统日志限制。使用发行版 `/usr/bin/python3`，避免虚拟环境找不到 GI。无需 root 运行解码；设备访问权由发行版 video/render 组及驱动配置决定。

## 3. RK3588 存储安全

先备份重要数据。曾发生 EXT4 I/O 错误的 SD 卡，必须先检查卡、电源、连接和文件系统。**不能靠清空 dmesg 或忽略错误继续跑。** 文件系统修复须从其他介质启动、对已卸载分区离线进行；不要对挂载中的根分区执行修复。本工具不自动修复、不清日志、不重新挂载、不重启机器。

每轮开始检查整个可读内核日志；出现 EXT4 error/warning、I/O error、MMC timeout、journal abort 等即拒绝。运行时每秒检查一次，发现错误或基线日志尾行从环形日志消失即中止；日志读取失败也退出。预检还拒绝相关只读文件系统、报告空间不足 512 MiB 的情况。硬件故障可能使内核或进程卡死，这不是任何用户态脚本能够绝对保证及时清理的情形。

建议程序、素材及结果都放在可靠 USB SSD 或空间充足的 tmpfs，避免持续写 SD 卡。tmpfs 写入结果会占内存，运行前查看 `free -h`、`df -h /dev/shm`，测试后尽快复制到可靠介质。保留的 dmesg 历史不是完整存储健康认证；本工具不能证明 SD 卡无潜在故障，也不测试实时磁盘读取吞吐。

## 4. 素材制作与循环规则

在工作站上离线准备，每类分辨率/帧率单独生成：

```bash
bash prepare.sh original.mp4 4K60 source-4k60.mp4
bash prepare.sh original.mp4 4K30 source-4k30.mp4
bash prepare.sh original.mp4 1080p30 source-1080p30.mp4
sha256sum source-*.mp4
```

需要 FFmpeg 的 `libx265` 编码器。脚本生成 5 秒 8-bit 4:2:0 HEVC、无音频、无 B 帧、闭合 GOP、首帧 IDR、整数 CFR、零起点时间戳。默认编码质量/码率随画面变化，应使用代表实际场景的运动和细节素材，公开码率、profile、level、GOP 和校验值。同一场景两板必须使用完全相同的文件；1080p30 与 4K30 不能简单在测试中丢弃 60fps 帧得到。

工具用 ffprobe 全片检查帧/包一对一、编码/尺寸/逐帧 CFR/PTS=DTS/无 B 帧/首帧关键帧，再用 GStreamer demux+parser 提取 AU，核对数量并检查首 AU 含 IDR。默认压缩缓存上限 128 MiB。素材要短，预检有 120 秒超时。

**不是反复启动播放器，也不是无缝 seek 假设。** 压缩 AU 一次读入内存，各路独立 appsrc 按统一单调时钟逐帧释放；每次循环重写连续 PTS/DTS，解码器不重启，IDR 开始新闭合 GOP。相同压缩片段在多个通道重放，不代表不同摄像头内容。由于严格拒绝 B 帧，该版本不能代表 B 帧重排、高位深、不同 profile、网络抖动或存储输入场景；这些需要扩展帧身份匹配后再测。

源片段循环时长与测试时长分开：5 秒素材可连续循环 1800 秒，planned 仍是每路 108000 帧（60fps）或 54000 帧（30fps）。测试过程中不编码、不重采样、不人为丢帧。appsrc/queue/appsink 都有有限缓冲，反压可能使 feed 落后；系统会记录调度延误和债务，绝不把输入实际变慢当作达到实时要求。

## 5. 四终端拍摄与运行

保持现有布局：左上 Pi 5 仪表盘，右上 RK3588 仪表盘；左下/右下保留原有硬件监控，不启动、替换或终止它们。工具只管理自己进程，不会杀掉其他 GStreamer 或监控进程。拍摄前自行停止无关解码负载，两板固定散热、电源、系统版本与频率策略。

解压后进入 `hevc-rt` 目录。先做 **10 秒冒烟测试**（不是 30 分钟合格结论）：

```bash
# Pi 5 左上；结果路径每次必须是不存在的新目录
bash run.sh --decoder v4l2slh265dec --case 1x4K60 \
  --source /mnt/ssd/source-4k60.mp4 --seconds 10 --repeats 1 \
  --out /dev/shm/pi5-smoke-01

# RK3588 右上
bash run.sh --decoder mppvideodec --case 1x4K60 \
  --source /mnt/ssd/source-4k60.mp4 --seconds 10 --repeats 1 \
  --out /dev/shm/rk-smoke-01
```

确认 dmesg 无错误、正常 EOS、实际插件和下方监控客户端符合预期，再测试整个矩阵。以下 RK3588 示例在 Pi 5 上只替换 `--decoder` 和输出目录标签：

```bash
bash run.sh --decoder mppvideodec --case 1x4K60 --source /mnt/ssd/source-4k60.mp4 --seconds 1800 --repeats 3 --out /dev/shm/rk-1x4k60-01
bash run.sh --decoder mppvideodec --case 4x4K30 --source /mnt/ssd/source-4k30.mp4 --seconds 1800 --repeats 3 --out /dev/shm/rk-4x4k30-01
bash run.sh --decoder mppvideodec --case 4x4K60 --source /mnt/ssd/source-4k60.mp4 --seconds 1800 --repeats 3 --out /dev/shm/rk-4x4k60-01
bash run.sh --decoder mppvideodec --case 8x1080p30 --source /mnt/ssd/source-1080p30.mp4 --seconds 1800 --repeats 3 --out /dev/shm/rk-8x1080p30-01
```

每个场景三轮 30 分钟约 90 分钟；四场景约 6 小时/设备（另加预检与排空）。两台可同时测试，但没有跨机器时钟同步；公平比较依赖相同本地测试契约。各路共享本机释放起点；启动阶段不隐藏冷启动迟到，无人为预热豁免。

可显式指定 `--deadline-ms 100 --release-ms 10 --drain-seconds 15`。超时后允许 15 秒排空，迟到帧仍不合格。外层独立进程看门狗在测试时长 + 排空 + 240 秒预检/启动预算后强制终止整组。一般故障也会保留已写结果；极端卡死/强杀可能只有 live.json 和部分帧 CSV，必须标记未完成。

`flock` 在每用户的私有 `XDG_RUNTIME_DIR` 防止整套脚本重复运行，锁覆盖三轮及看门狗。请只通过 run.sh 启动；不同用户/绕过包装器不受这把锁保护。Ctrl+C 会停止本工具子进程，5 秒内未退出则强杀，不会按进程名误杀别的程序。不要在 sudo 下意外换用户后重复启动。

## 6. 输出和判读

- `summary.json / summary.csv`：逐轮逐路最终结果，退出码、完成原因；异常时停止后续轮次。
- `trial-N/environment.json`：内核、GStreamer、插件完整 inspect、挂载信息、参数、素材 SHA256 和 ffprobe stream 信息。
- `trial-N/lane-N-frames.csv`：逐帧 ID、PTS、接收单调时钟、计划至接收延迟及 late 标志。单调时钟只适合本机时差。
- `trial-N/timeline.csv / live.json`：每秒计数、队列、调度债务、状态。峰值 backlog 是采样峰值，不是严格连续峰值。
- `trial-N/result.json`：各阶段原始/唯一计数、最终缺失 ID、异常、最大延迟、PASS 与 30 分钟资格。
- `interrupted.json`：主动中断标记；不得把部分结果当完成。

退出码：0 所有要求轮次 PASS；1 性能不合格/缺失/超时；2 配置或基础设施错误；130 中断。看门狗强杀轮次在汇总中记 124。没有 result.json、子进程非零退出、非 EOS 完成均不能认定合格，即使界面瞬时计数一致。

界面不以瞬时 FPS 判断零丢帧，累计平均 FPS 也无法替代 ID 和截止检查。`decoded=delivered=planned` 且 late>0 表示最终完整但实时性失败；`backlog` 上升代表排队；`schedule_debt` 上升表示输入落后；尚在运行时的 missing 不能当成最终丢帧数。最终缺失只说明观测边界未完成交付，未自动定位驱动/插件/输入哪一层根因。

## 7. 上板验收方法

1. 在健康存储上完成 1×4K60 的 10 秒冒烟；人工核对 planned=600、逐路正常 EOS、PTS 连续；查看硬件监控确认所选驱动实际打开，不能只凭插件名字证明芯片利用率。
2. 以同一文件 SHA256 在两板各做四种场景三轮。保存原始 CSV/JSON 和下方监控记录，包含温度、频率、CPU、内存；监控客户端数/时钟不等于硬解利用率。
3. 验证负向路径：测试中 Ctrl+C；同一用户再运行一次脚本应锁失败；错误分辨率、B 帧素材、不可读 dmesg 应拒绝；缺插件应报错。不要人为制造 SD I/O 错误。
4. 可用 `--deadline-ms 0.001` 做极严截止的负向检查：通常应出现 late 并失败，不能因帧总数相等而通过。它是测量逻辑检查，不是性能结论。
5. 超负载或调度受限时核对 debt/backlog/late 与排空结果；超时不可标 DONE/PASS。不要暂停整个父进程来验证看门狗（父进程也被暂停将无法监督）。
6. 在 Linux 安装依赖后运行 `python3 test_accounting.py`。此测试使用假的 GStreamer buffer 验证计数契约，不代替实际解码集成测试。

正式视频只报告已完成的实测结果，注明码流、输入模型、截止阈值、每路完成/迟到/缺失和重复轮次。不要从官方“4K60/8K60”直接推导多路合格能力。

## 参考文档

- GStreamer appsink： https://gstreamer.freedesktop.org/documentation/app/appsink.html
- GStreamer BaseSink： https://gstreamer.freedesktop.org/documentation/base/gstbasesink.html
- GStreamer pipeline manipulation： https://gstreamer.freedesktop.org/documentation/application-development/advanced/pipeline-manipulation.html
- GStreamer v4l2slh265dec： https://gstreamer.freedesktop.org/documentation/v4l2codecs/v4l2slh265dec.html
- 板厂 MPP GStreamer 说明： https://community.t-firefly.com/en/docs/software/multimedia/GStreamer/gstreamer-rockchip-decoding

软件版本及板厂实现可能不同；本机插件 inspect 与实际时间戳验证为准。
