---
layout:     post
title:      "以容器化方式运行 pytorch 程序"
date:       2024-11-09 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - gpu调度
---

**目录**
- [概述](#概述)
- [1. pytorch 程序代码](#1-pytorch-程序代码)
- [2. Dockerfile 配置](#2-dockerfile-配置)
- [3. 机器配置](#3-机器配置)
  - [3.1 安装 gpu/cuda 驱动](#31-安装-gpucuda-驱动)
  - [3.2 nvidia-container-toolkit](#32-nvidia-container-toolkit)
  - [3.3 部署 gpu device-plugin](#33-部署-gpu-device-plugin)
- [4. 通过 job 运行程序](#4-通过-job-运行程序)
- [总结](#总结)
- [备注](#备注)
  - [配置 containerd 镜像代理](#配置-containerd-镜像代理)
  - [nvidia-smi](#nvidia-smi)

### 概述
[pytorch](https://github.com/pytorch/pytorch) 是 Facebook 开源的深度学习框架，在计算机视觉、自然语言处理、强化学习等领域有广泛的应用。

本文总结一下在容器化环境下如何运行 pytorch，重点关注如何使用集群中的 gpu 资源。本文使用的机器是在阿里云购买的一台 [ecs.gn6i-c4g1.xlarge](https://help.aliyun.com/zh/egs/gpu-accelerated-compute-optimized-instance-families?spm=a2c4g.11186623.0.0.7c085fe0weRq7L#09b66d8b1alce) gpu 机器（12¥/小时），跟社区通过安装 gpu-operator 来配置 gpu 环境的方式不太一样，不过不影响我们理解如何运行容器化 pytorch 程序。

### 1. pytorch 程序代码
首先我们写一段 pytorch 程序代码，下面代码首先打印节点上的 gpu 信息，然后在 gpu 上计算一个向量加法，我并不懂如何写 pytorch 代码，只是从应用开发者的角度去看一下都有哪些问题需要面对。

将下面代码保存到 `gpu_info.py` 文件。
```python
import torch

# 打印 PyTorch 版本和 CUDA 是否可用
print(f"PyTorch Version: {torch.__version__}")
print(f"CUDA Available: {torch.cuda.is_available()}")

# 如果 CUDA 可用，打印更详细的 GPU 和 CUDA 信息
if torch.cuda.is_available():
    print(f"CUDA Device Count: {torch.cuda.device_count()}")
    
    # 获取每个设备的详细信息
    for i in range(torch.cuda.device_count()):
        print(f"\nDevice {i} - {torch.cuda.get_device_name(i)}")
        print(f"  Memory Allocated: {torch.cuda.memory_allocated(i) / (1024 ** 2):.2f} MB")
        print(f"  Memory Cached: {torch.cuda.memory_reserved(i) / (1024 ** 2):.2f} MB")
        print(f"  CUDA Compute Capability: {torch.cuda.get_device_capability(i)}")
        print(f"  Device Properties: {torch.cuda.get_device_properties(i)}")
else:
    print("CUDA is not available. Running on CPU.")

# 创建两个向量，进行加法运算
vector1 = torch.tensor([1.0, 2.0, 3.0])
vector2 = torch.tensor([4.0, 5.0, 6.0])

# 如果 GPU 可用，将向量转移到 GPU
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
vector1 = vector1.to(device)
vector2 = vector2.to(device)

# 执行向量加法
result = vector1 + vector2

# 打印向量和加法结果
print("\nVector 1:", vector1)
print("Vector 2:", vector2)
print("Result of Addition:", result)
```

### 2. Dockerfile 配置
Dockerfile 总体很简单，即使用 pytorch 官方的镜像，然后把我们的代码拷贝进去，这个镜像非常大，有 7.6G，里面包括了：python 运行环境（以及常用 python 包）、pytorch 框架、cuda 编程框架等。这个镜像简单好用，缺点就是大，一般这种情况下需要做镜像预加载了。

```sh
# 使用 PyTorch 官方镜像作为基础镜像
FROM pytorch/pytorch:latest

# 设置工作目录
WORKDIR /app

# 复制 Python 程序到容器内
COPY gpu_info.py .

# 安装任何额外的依赖（如果需要）
RUN pip install --no-cache-dir torch

# 设置容器启动命令，运行 Python 脚本
CMD ["python", "gpu_info.py"]
```
使用上面上面 Dockerfile build 镜像并将镜像上传到合适的仓库。
### 3. 机器配置

#### 3.1 安装 gpu/cuda 驱动
安装驱动有多种方式，具体包括：

1. 手动下载驱动进行安装。在 [nvidia 官方主页](https://www.nvidia.cn/drivers/lookup/)下载 `.run` 文件，比如 `NVIDIA-Linux-x86_64-550.54.14.run`，然后通过下面命令。
```s
sh NVIDIA-Linux-x86_64-550.54.14.run
```
2. 使用 [nvidia-operator](https://github.com/NVIDIA/gpu-operator) 自动安装，nvidia-operator 包含 NVIDIA Driver Installer 组件能自动为 gpu 机器安装驱动。
3. 使用公有云的机器，公有云的机器一般都预装了 gpu/cuda 驱动（以及 nvidia-container-runtime），不需要我们配置，本文是通过这种方式工作的。

阿里云机器购买之后，我们通过 nvidia-smi 命令查看输出如下：
```s
[root@iZbp173oxsaeu2ftxh8majZ ~]# nvidia-smi
Sat Jan 25 12:12:15 2025       
+---------------------------------------------------------------------------------------+
| NVIDIA-SMI 535.161.07             Driver Version: 535.161.07   CUDA Version: 12.2     |
|-----------------------------------------+----------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |         Memory-Usage | GPU-Util  Compute M. |
|                                         |                      |               MIG M. |
|=========================================+======================+======================|
|   0  Tesla T4                       On  | 00000000:00:07.0 Off |                    0 |
| N/A   31C    P8               9W /  70W |      0MiB / 15360MiB |      0%      Default |
|                                         |                      |                  N/A |
+-----------------------------------------+----------------------+----------------------+
                                                                                         
+---------------------------------------------------------------------------------------+
| Processes:                                                                            |
|  GPU   GI   CI        PID   Type   Process name                            GPU Memory |
|        ID   ID                                                             Usage      |
|=======================================================================================|
|  No running processes found                                                           |
+---------------------------------------------------------------------------------------+
```
从上面输出中能够看到 gpu/cuda 的驱动版本，以及只有一个 gpu 卡（只有一个编号 `0`），关于 nvidia-smi 命令的输出可以本文的备注部分。

#### 3.2 nvidia-container-toolkit
[nvidia-container-toolkit](https://github.com/NVIDIA/nvidia-container-toolkit) 项目包含 nvidia-container-runtime 以及配置 runtime 的一些工具，其目的是将 gpu 设备挂载到容器内部，让容器的程序能够使用 gpu。

nvidia-container-runtime 是一个容器运行时，其符合 oci 规范，跟 runc 是同等地位，nvidia-container-runtime 的实现方式是修改容器的 spec， 在 spec 中加一个 nvidia-container-runtime-hook，该 hook 的作用是挂载 gpu 设备，然后剩下的工作就是交给 runc 了，nvidia-container-runtime 最终也是调用 runc 来启动容器。

在阿里云的 gpu 机器中，containerd 的容器运行时已经被设置为 nvidia-runtime，这点我们可以从 containerd 的配置文件 `/etc/containerd/config.toml` 中看出来。
```s
[root@iZbp18vb0f6lapereqtvdmZ ~]# cat /etc/containerd/config.toml
version = 2

root = "/var/lib/containerd"
state = "/run/containerd"
disabled_plugins = []
required_plugins = ["io.containerd.grpc.v1.cri"]
oom_score = -999

[plugins]

  [plugins."io.containerd.grpc.v1.cri"]
    sandbox_image = "registry-cn-hangzhou-vpc.ack.aliyuncs.com/acs/pause:3.9"
    ignore_image_defined_volumes = true
    disable_apparmor = true

    [plugins."io.containerd.grpc.v1.cri".containerd]
      snapshotter = "overlayfs"
      default_runtime_name = "runc"
      disable_snapshot_annotations = true
      discard_unpacked_layers = false

      [plugins."io.containerd.grpc.v1.cri".containerd.runtimes]
        [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc]
          runtime_type = "io.containerd.runc.v2"
          privileged_without_host_devices = false
          [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
            BinaryName = "nvidia-container-runtime"
            NoPivotRoot = false
            NoNewKeyring = false
            SystemdCgroup = true
```
如果我们使用 gpu-operator 来配置 gpu 运行环境，nvidia container runtime 也会被自动配置。

#### 3.3 部署 gpu device-plugin
在 gpu 机器节点中，我们需要部署 [gpu-device-plugin](https://github.com/NVIDIA/k8s-device-plugin) 来上报 gpu 信息，这个组件 gpu-operator 也会帮我们部署，在阿里云的机器中，机器默认配置了 gpu 资源，并以核数的方式进行资源汇报，不过在需要将 gpu 进行虚拟化或者分时复用的情况下，gpu 资源不能这么上报。
```yaml
  allocatable:
    aliyun/member-eni: "13"
    cpu: 3920m
    ephemeral-storage: "380116949592"
    hugepages-1Gi: "0"
    hugepages-2Mi: "0"
    memory: 14368780Ki
    nvidia.com/gpu: "1"
    pods: "13"
```
在阿里云的 ack 集群中，我没有安装 ai 套件，节点也没有部署 device-plugin 相关组件，我估计这可能是基本的 gpu 资源上报，正常情况下我们是需要安装阿里云的 ai 套件的，以支持更多的调度策略，这里为了简单方便，并没有安装了。

### 4. 通过 job 运行程序
通过下面 job 运行 pytorch 程序，同时检查输出。
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: pytorch-cuda-job
spec:
  template:
    spec:
      imagePullSecrets:
      - name: aliyun-image-secret
      containers:
      - name: pytorch-cuda-container
        image: docker.io/lr90:pytorch-add-2.0
        resources:
          limits:
            nvidia.com/gpu: 1  # 请求一个 GPU
      restartPolicy: Never
  backoffLimit: 4
```
输出如下，符合我们预期。
```s
PyTorch Version: 2.2.1
CUDA Available: True
CUDA Device Count: 1

Device 0 - Tesla T4
  Memory Allocated: 0.00 MB
  Memory Cached: 0.00 MB
  CUDA Compute Capability: (7, 5)
  Device Properties: _CudaDeviceProperties(name='Tesla T4', major=7, minor=5, total_memory=15102MB, multi_processor_count=40)

Vector 1: tensor([1., 2., 3.], device='cuda:0')
Vector 2: tensor([4., 5., 6.], device='cuda:0')
Result of Addition: tensor([5., 7., 9.], device='cuda:0')
```

### 总结
本文从应用程序出发，了解了作为模型开发者是如何开发 pytorch 程序，并将应用进行容器化的过程。在此基础上，作为 K8s 运维的我们应该准备什么样的环境以承接容器化应用。基本来说，模型开发者只需要正常编写 pytorch 代码，并使用 pytorch 官方镜像构建镜像；然后 K8s 运维人员需要准备 gpu 运行环境，一般来说 gpu-operator 就能满足其运行条件。

鉴于没有 gpu 机器进行测试，在阿里云购买了一台机器，因此与传统 gpu-operator 使用方式有些区别，不过传统 gpu-operator 已经相对比较成熟，不影响我们理解整个部署过程。

### 备注
#### 配置 containerd 镜像代理
如果镜像拉不下来，需要配置 containerd 代理，配置方式如下：
1. vi /etc/systemd/system/containerd.service，添加代理配置
   ```s
   [Service]
   Environment="HTTP_PROXY=http://your-proxy-address:port"
   Environment="HTTPS_PROXY=http://your-proxy-address:port"
   Environment="NO_PROXY=localhost,127.0.0.1,.local"
   ```
2. 重新启动 containerd，命令为
   ```s
   systemctl daemon-reload
   systemctl restart containerd
   ```

#### nvidia-smi 
`nvidia-smi`（nvidia system management interface）是一个由 nvidia 提供的命令行工具，用于监控和管理 nvidia gpu。它为用户提供了一个简单的方法来查看 gpu 的状态、性能指标、温度、使用情况等信息，并可以进行一些基本的控制操作。下面是一个 `nvidia-smi` 命令的输出示例及其详细解析：
```s
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 460.32.03    Driver Version: 460.32.03    CUDA Version: 11.2     |
|-------------------------------+----------------------+----------------------|
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf  Pwr:Usage/Cap| Memory-Usage | GPU-Util  Compute M. |
|===============================+======================+======================|
|   0  Tesla K80           Off  | 00000000:00:1E.0 Off |                    0 |
| N/A   29C    P8    30W / 149W |    0MiB / 11441MiB |      0%      Default |
+-------------------------------+----------------------+----------------------+
```
下面表格介绍了各个字段的含义：

| **类别**                     | **字段**                     | **说明**                                                                 |
|------------------------------|------------------------------|-------------------------------------------------------------------------|
| **版本和驱动信息** | `NVIDIA-SMI`                | 当前使用的 NVIDIA 驱动版本。                                             |
|                              | `Driver Version`            | NVIDIA 显卡驱动的版本号。                                               |
|                              | `CUDA Version`              | 当前支持的 CUDA 版本号。                                                |
| **GPU 信息**                 | `GPU`                       | 显示 GPU 的编号。例如 `0` 表示系统中第一个 GPU。                         |
|                              | `Name`                      | 显示 GPU 的型号。例如 `Tesla K80`。                                     |
|                              | `Persistence-M`             | 显示 GPU 持久模式（Persistence Mode）状态。通常是 `Off`，表示 GPU 在空闲时会进入省电模式。 |
|                              | `Bus-Id`                    | GPU 的 PCI 总线 ID，用于识别 GPU 的物理位置。                           |
|                              | `Disp.A`                    | 显示是否有显示输出（Display Active）。通常是 `Off`，表示 GPU 没有直接连接显示器。 |
|                              | `Volatile Uncorr. ECC`      | 显示是否存在非易失性、不可纠正的 ECC 错误（错误检查和纠正码）。通常是 `0`，表示没有错误。 |
| **温度、风扇、功耗、性能** | `Fan`                       | 显示 GPU 风扇的转速（如果有的话）。例如 `N/A` 表示没有风扇数据。         |
|                              | `Temp`                      | 显示 GPU 当前的温度（单位：摄氏度）。                                   |
|                              | `Perf`                      | 显示 GPU 当前的性能状态（Performance State）。`P8` 是最低的性能状态，`P0` 是最高性能状态。 |
|                              | `Pwr:Usage/Cap`             | 显示 GPU 当前功耗与最大功耗的比率。例如 `30W / 149W` 表示当前功耗为 30 瓦，最大支持功耗为 149 瓦。 |
| **显存使用情况**             | `Memory-Usage`              | 显示显存的使用情况。例如 `0MiB / 11441MiB` 表示当前显存使用为 0MB，总共可用 11441MB。 |
|                              | `GPU-Util`                  | 显示 GPU 的利用率（使用百分比）。如果是 `0%`，表示 GPU 当前没有进行计算任务。 |
|                              | `Compute M.`                | 显示 GPU 是否处于计算模式。一般为 `Default`。                           |


常用的 `nvidia-smi` 命令选项：
```s
# 列出所有可用的 NVIDIA GPU。
nvidia-smi -L

# 显示更详细的 GPU 信息，包括内存、温度、电源、驱动等详细信息。
nvidia-smi -q

# 每秒自动刷新一次状态信息。
nvidia-smi --loop=1

# 重置指定编号的 GPU（编号 `0` 为第一个 GPU）。
nvidia-smi --gpu-reset -i 0

# 查询 GPU 利用率，并以 CSV 格式输出。
nvidia-smi --query-gpu=utilization.gpu --format=csv

# 启用 GPU 持久模式，防止 GPU 在空闲时进入省电模式。
nvidia-smi -pm 1

# 设置指定 GPU（此处为 GPU 0）的显存上限为 50%。
nvidia-smi -i 0 --memory-percent=50
```
