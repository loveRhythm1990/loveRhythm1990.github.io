---
layout:     post
title:      "K8s 环境下通过 nvidia device plugin 使用 gpu"
date:       2025-01-29 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - gpu调度
---

**目录**
- [device plugin 概述](#device-plugin-概述)
  - [nvidia 实现](#nvidia-实现)
- [部署安装](#部署安装)
  - [前置条件 nvidia-toolkit](#前置条件-nvidia-toolkit)
  - [通过 helm 安装](#通过-helm-安装)
- [配置共享 gpu](#配置共享-gpu)
  - [Time-Slicing](#time-slicing)
  - [MPS(Multi-Process Service)](#mpsmulti-process-service)
  - [MIG(Multi-Instance GPUs)](#migmulti-instance-gpus)
- [总结](#总结)



### device plugin 概述
[device plugin](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) 是 Kubernetes 集群使用外部资源的扩展机制，其工作过程为：
1. device plugin 通过 unix socket 向 kubelet 注册自己，并上报节点可用扩展资源。
2. kubelet 向 kube-apiserver 上报并被 kube-scheduler 监听，从而执行调度决策。
3. kubelet 通过 allocate 接口指示 device plugin 分配外部资源。

整体工作过程图如下，图片来自《[Kubernetes 1.26: Device Manager graduates to GA](https://kubernetes.io/blog/2022/12/19/devicemanager-ga/)》。下面图中右边框里五个灰色的接口是 device plugin 需要实现的接口，其中只有 ListAndWatch 以及 Allocate 是必选的。
![java-javascript](/pics/deviceplugin-framework-overview.svg){:height="50%" width="50%"}

#### nvidia 实现
[nvidia-device-plugin](https://github.com/NVIDIA/k8s-device-plugin) 是 nvidia 为在 Kubernetes 集群中使用 gpu 提供的 plugin。这里重点关注下 Allocate 方法的实现。

在该方法中，通过 DEVICE_LIST_STRATEGY（nvidia plugin 可配置参数）返回设备标志，主要有两种方式：env、mounts，对于 env 的方式，需要配置环境变量：`NVIDIA_VISIBLE_DEVICES=0`，这里的 `0` 是 gpu 编号，如果有多个设备设备之间需要使用逗号隔开，如`0,1,2`；还有一种策略是填 uuid，由环境变量 DEVICE_ID_STRATEGY 控制使用编号还是 uuid。对于 mounts 则表明容器需要挂载的 gpu 设备。nvidia 的主要实现在 getAllocateResponse 方法中。

在 device-plugin 配置 NVIDIA_VISIBLE_DEVICES 环境变量之后，容器运行时 nvidia-container-runtime 会根据此环境变量将特定 gpu 设备挂载到容器中，因此应用只能看到特定的设备。

```go
func (plugin *nvidiaDevicePlugin) Allocate(ctx context.Context, reqs *pluginapi.AllocateRequest) (*pluginapi.AllocateResponse, error) {
	responses := pluginapi.AllocateResponse{}
	for _, req := range reqs.ContainerRequests {
		if err := plugin.rm.ValidateRequest(req.DevicesIDs); err != nil {
			return nil, fmt.Errorf("invalid allocation request for %q: %w", plugin.rm.Resource(), err)
		}
		response, err := plugin.getAllocateResponse(req.DevicesIDs)
		if err != nil {
			return nil, fmt.Errorf("failed to get allocate response: %v", err)
		}
		responses.ContainerResponses = append(responses.ContainerResponses, response)
	}
	return &responses, nil
}

func (plugin *nvidiaDevicePlugin) getAllocateResponse(requestIds []string) (
  *pluginapi.ContainerAllocateResponse, error) {
	
  deviceIDs := plugin.deviceIDsFromAnnotatedDeviceIDs(requestIds)

	// Create an empty response that will be updated as required below.
	response := &pluginapi.ContainerAllocateResponse{
		Envs: make(map[string]string),
	}
	// 新增一个环境变量：NVIDIA_VISIBLE_DEVICES
	if plugin.deviceListStrategies.Includes(spec.DeviceListStrategyEnvVar) {
		plugin.updateResponseForDeviceListEnvVar(response, deviceIDs...)
		plugin.updateResponseForImexChannelsEnvVar(response)
	}

	return response, nil
}

// 新增环境变量的方法
func (plugin *nvidiaDevicePlugin) updateResponseForDeviceListEnvVar(
  response *pluginapi.ContainerAllocateResponse, deviceIDs ...string) {
	
  response.Envs[deviceListEnvVar] = strings.Join(deviceIDs, ",")
}
```

在 containerd 环境下，可以通过 crictl inspect 命令查看容器所使用的 gpu 编号。plugin 将设备编号返回给 kubelet 之后，后者会将环境变量配置到容器的运行时配置中，容器运行时 nvidia-container-runtime 在看到环境变量之后，会将对应 gpu 设备挂载到容器内部。nvidia-container-runtime 是 runc 的一个 warpper，负责拦截请求，并将设备挂载到容器内部。或者也可以直接 exec 到容器内部查看这个环境变量。
```s
[root@iZbp1gc07an180eleh2t4cZ ~]# crictl inspect 687872d09953d | grep -C 1 NVIDIA_VISIBLE_DEVICES
        {
          "key": "NVIDIA_VISIBLE_DEVICES",
          "value": "0"
--
          "NLTK_DATA=/app/nltk_data",
          "NVIDIA_VISIBLE_DEVICES=0",
          "MG_PARSE_SERVICE_PORT_8000_TCP_PROTO=tcp",
```

在一些场景中，我们需要限制暴露到 Kubernetes 集群中的 gpu 卡数，或者只将特定的卡暴露给 Kubernetes 集群中，剩下的卡供主机上的其他服务使用，此时我们仍然通过刚给 nvidia-device-plugin daemonset 配置环境变量 NVIDIA_VISIBLE_DEVICES 来实现，因为 nvidia-container-runtime 能拦截 nvidia-device-plugin daemonset pod 的创建，在看到这个环境变量之后，会限制 daemonset pod 所能看到的 gpu 卡，因此 daemonset pod 就认为机器上只有这些卡，跟把 daemonset pod 当做 gpu 应用一样。

### 部署安装
#### 前置条件 nvidia-toolkit
nvidia plugin 不会在节点上安装 nvidia 驱动以及配置容器运行时，这个需要开发人员提前配置好，（nvidia operator 会做这些事情，因此 operator 看上去是更好的实践）。目前的约束条件有：
* NVIDIA drivers ~= 384.81
* nvidia-docker >= 2.0 或者 nvidia-container-toolkit >= 1.7.0 (>= 1.11.0 to use integrated GPUs on Tegra-based systems)
* nvidia-container-runtime configured as the default low-level runtime
* Kubernetes version >= 1.10

下面介绍 **配置容器运行时为 nvidia-contaienr-runtime** 的方法：
1. 首先按照文档 [How to Install NVIDIA Container Toolkit and Use GPUs with Docker Containers](https://www.gpu-mart.com/blog/install-nvidia-container-toolkit) 安装 container toolkit。安装 toolkit 之后会自动安装命令 nvidia-ctk。
2. 配置容器运行时为 nvidia-container-runtime：
   ```s
   sudo nvidia-ctk runtime configure --runtime=docker
   ```
   该命令会修改 /etc/docker/daemon.json 文件并添加下面配置：
   ```json
   {
    "runtimes": {
        "nvidia": {
            "args": [],
            "path": "nvidia-container-runtime"
        }
    }
   }
   ```

#### 通过 helm 安装
这部分内容参考官方文档 [Deployment via helm](https://github.com/NVIDIA/k8s-device-plugin?tab=readme-ov-file#deployment-via-helm)。文档中有很多配置项，使用的时候需要注意一下。
```s
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update

helm upgrade -i nvdp nvdp/nvidia-device-plugin \
  --namespace nvidia-device-plugin \
  --create-namespace \
  --version 0.17.0
```
另外 device plugin 支持安装 [GFD(gpu-feature-discovery)](https://github.com/NVIDIA/gpu-feature-discovery)，并且在启用 GFD 时，会默认安装 [NFD(Node Feature Discovery)](https://kubernetes-sigs.github.io/node-feature-discovery/stable/get-started/index.html)。后者主要生成节点相关特性相关 label 如：cpu 架构、指令集、内核版本，前者主要生成 gpu 相关 label，如：gpu 型号、卡数、驱动版本等。
```s
helm upgrade -i nvdp nvdp/nvidia-device-plugin \
  --version=0.17.0 \
  --namespace nvidia-device-plugin \
  --create-namespace \
  --set gfd.enabled=true
```

### 配置共享 gpu
该部分内容介绍通过 nvidia-device-plugin 多 pod 共享一张 gpu 卡的几种方式。
#### Time-Slicing
time-slicing 是以时间分片的方式共享 gpu（跟非 K8s 环境下使用 gpu 的行为一致）。以下面配置为例，通过 `replicas: 5` 将一个 gpu 虚拟为 5 个 gpu，假设节点原来有 2 个 gpu 卡，则现在有 2*5=10 个，即上报给 kubelet 的资源为 `nvidia.com/gpu.shared=10`，这里为什么是 nvidia.com/gpu.shared 而不是 nvidia.com/gpu，是因为下面配置了 `renameByDefault: true`，即重命名虚拟后的资源。

同时还有一个配置项比较重要 `failRequestsGreaterThanOne: true`，这个配置项的含义是，如果 pod 在资源申请的时候配置的 nvidia.com/gpu.shared 或者 nvidia.com/gpu 大于 1，则创建时会失败，也就是说，在这种配置下，只允许容器配置一个 gpu 分片，关于为什么这么做 [With CUDA Time-Slicing](https://github.com/NVIDIA/k8s-device-plugin?tab=readme-ov-file#with-cuda-time-slicing) 文档有说明，当配置多个时间片时，并不能保证每个 client 有更好的优先级，因此最佳实践就是设置为 1。

device plugin 需要挂载下面 configmap，并配置启动参数：--config=/etc/nvidia/config.yaml。
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: nvidia-device-plugin-config
  namespace: kube-system
data:
  config.yaml: |
    version: v1
    flags:
      migStrategy: none  # 禁用 MIG（Multi-Instance GPU）
    sharing:
      timeSlicing:
        renameByDefault: true
        failRequestsGreaterThanOne: true
        resources:
        - name: nvidia.com/gpu
          replicas: 5 
```

nvidia-device-plugin time slice 的实现跟在主机上直接使用 gpu 类似，仅仅是为了突破 K8s scheduler 的限制，因此没有做额外的隔离，以及优先级等（比如获得更多的时间片，这是无法做到的）。这一点在看完 device plugin 的实现之后就了解了。

在 device plugin 中，主要是通过 [https://github.com/NVIDIA/go-nvml](https://github.com/NVIDIA/go-nvml) 库来获取 gpu 信息，这是一个用 cgo 把 c 封装起来的一个项目。对应的在 kubelet 实现中，也是通过 NVMLResourceManager 来管理 gpu 资源。在其初始化方法 NewNVMLResourceManagers 中，会构建一个 DeviceMap，在构建这个 map 的时候，会根据倍数将设备翻倍，分到同一个 gpu 的应用就会共享此 gpu。

```go
// internal/rm/nvml_manager.go
func NewNVMLResourceManagers(infolib info.Interface, 
nvmllib nvml.Interface, 
devicelib device.Interface, 
config *spec.Config) ([]ResourceManager, error) {
	
  ret := nvmllib.Init()
	if ret != nvml.SUCCESS {
		return nil, fmt.Errorf("failed to initialize NVML: %v", ret)
	}
	// 构建
	deviceMap, err := NewDeviceMap(infolib, devicelib, config)
	if err != nil {
		return nil, fmt.Errorf("error building device map: %v", err)
	}

	// 省去了一些代码
	return rms, nil
}
```
设备翻倍的大致代码如下，其中每个虚拟设备的编号由 NewAnnotatedID 生成，在 allocate 设备时，会再根据这个标号拿到真正的设备 ID。
```go
func updateDeviceMapWithReplicas(replicatedResources *spec.ReplicatedResources, 
oDevices DeviceMap) (DeviceMap, error) {
	devices := make(DeviceMap)

	// Walk shared Resources and update devices in the device map as appropriate.
	for _, resource := range replicatedResources.Resources {
		r := resource
		// Get the IDs of the devices we want to replicate from oDevices
		ids, err := oDevices.getIDsOfDevicesToReplicate(&r)

		for _, id := range ids {

			// 针对每一个 replica 生成一个新的设备
			for i := 0; i < r.Replicas; i++ {
				annotatedID := string(NewAnnotatedID(id, i))
				replicatedDevice := *(oDevices[r.Name][id])
				replicatedDevice.ID = annotatedID
				replicatedDevice.Replicas = r.Replicas
				devices.insert(name, &replicatedDevice)
			}
		}
	}

	return devices, nil
}

func NewAnnotatedID(id string, replica int) AnnotatedID {
	return AnnotatedID(fmt.Sprintf("%s::%d", id, replica))
}
```


#### MPS(Multi-Process Service)
nvidia mps 是一种用于优化多进程共享 gpu 资源的技术。它的主要目的是提高 gpu 的利用率和吞吐量，特别是在多进程、多任务场景下（如深度学习推理、科学计算等），mps 的核心思想是将多个进程的 gpu 任务合并到一个上下文中执行，从而减少上下文切换开销并提高 gpu 利用率。mps 对应用开发者是透明的，应用开发者只需要使用标准的 cuda api 编程即可。

启用 mps 需要在 gpu 节点上启动 mps server，可通过命令 [nvidia-cuda-mps-control](https://man.archlinux.org/man/extra/nvidia-utils/nvidia-cuda-mps-control.1.en) 来实现。
```s
[root@iZbp18oviqo0duk6ssk60gZ ~]# nvidia-cuda-mps-control
Cannot find MPS control daemon process

# 以 daemon 形式启动 mps server，ps -ef | grep mps
# nvidia-cuda-mps-control -d
```
在 nvidia-device-plugin 的实现中，也是通过这个 binary 来启动 mps daemon 的。
```go 
// cmd/mps-control-daemon/mps/daemon.go
const (
	mpsControlBin = "nvidia-cuda-mps-control"
)

// Start starts the MPS deamon as a background process.
func (d *Daemon) Start() error {

	// 启动 mps daemon
	mpsDaemon := exec.Command(mpsControlBin, "-d")

	return nil
}
```

使用 mps 的方式跟使用 time-slicing 的方式基本一致，需要配置配置文件中的 mps 部分，配置 replicas:10 之后，上报的 gpu 资源将是原来的 10 倍。
```yaml
version: v1
sharing:
  mps:
    renameByDefault: true
    resources:
    - name: nvidia.com/gpu
      replicas: 10
```

mps 的隔离效果优于 time-slicing（time-slicing 没有实现隔离），但其实现比较复杂，在 nvidia-device-plugin 中还是 alpha 状态。具体看 v0.15.0 的 [release node](https://github.com/NVIDIA/k8s-device-plugin/releases/tag/v0.15.0)。

mps 的官方文档为 [Multi-Process Service](https://docs.nvidia.com/deploy/mps/index.html)，比较详细。

#### MIG(Multi-Instance GPUs)

mig 是从安培(Ampere，2020 年)架构开始支持的，具体是从 A100/A30 开始支持，A10 虽然是 Ampere 架构，但是不支持 mig。可以通过下面命令（-q 查看详情）查看 gpu 是否支持 mig，如果 mig mode 是 N/A 则表示不支持 mig。
```s
nvidia-smi -q | grep "Mig Mode"
# 输出示例：
#    Mig Mode                 : Enabled
#         Current             : 3
#         Pending             : 3
```

在 nvidia-device-plugin 的文档中，没有很好的解释如何使用 mig 来共享 gpu，不过在 nvidia-operator 的文档 [GPU Operator with MIG](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-mig.html) 倒是介绍的很详细，在大多数情况下，我们也是通过 operator 来使用 gpu，因此本文就参考 operator 文档介绍下。

首先 mig 有三种策略：none,single,mixed，其中 none 表示禁用。single 表示一个 gpu 卡划分成统一中 profile，mixed 表示一个 gpu 卡可划分为多种 profile。gpu 卡类型与支持的 profile 可以参考 nvidia 文档 [Supported MIG Profiles](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/#supported-mig-profiles)。

这里以 nvidia-operator 为例说明如何配置 mig，在介绍如何配置之前，我们先看 mig 相关的几个关键 node label:
* nvidia.com/mig.strategy: mig 策略，取值 `single` 或者 `mixed`。
* nvidia.com/mig.config: 指定 single profile 的配置，比如 `all-1g.10gb`，或者指定在 configmap 中自定义的 mig 配置，比如取值 `custom-mig`。另外取值 `all-disabled` 表示禁用。在 Kubernetes 集群中，存在组件 mig-manager 会监听这个 label，并在 label 发生变化时，重新将配置应用到节点，必要时会重启所有 gpu client、甚至节点。我们可以通过下面命令查看 mig-manager 的日志。
  ```s
  kubectl logs -n gpu-operator -l app=nvidia-mig-manager -c nvidia-mig-manager
  ```
* nvidia.com/mig.config.state：表示 mig 配置状态，正常情况是 `success` 状态，另外还有 `pending`、`rebooting`。
* nvidia.com/mig-1g.10gb.count：mig 实例数这里是指 `mig-1g.10gb` 规格的实例数，这个还有其他相关 label，比如下面是官方给出的。
  ```s
  "nvidia.com/mig-1g.10gb.count": "2",
  "nvidia.com/mig-1g.10gb.engines.copy": "1",
  "nvidia.com/mig-1g.10gb.engines.decoder": "1",
  "nvidia.com/mig-1g.10gb.engines.encoder": "0",
  "nvidia.com/mig-1g.10gb.engines.jpeg": "1",
  "nvidia.com/mig-1g.10gb.engines.ofa": "0",
  "nvidia.com/mig-1g.10gb.memory": "9984",
  "nvidia.com/mig-1g.10gb.multiprocessors": "16",
  "nvidia.com/mig-1g.10gb.product": "NVIDIA-H100-80GB-HBM3-MIG-1g.10gb",
  "nvidia.com/mig-1g.10gb.replicas": "1",
  "nvidia.com/mig-1g.10gb.sharing-strategy": "none",
  "nvidia.com/mig-1g.10gb.slices.ci": "1",
  "nvidia.com/mig-1g.10gb.slices.gi": "1",
  "nvidia.com/mig-2g.20gb.count": "1",
  "nvidia.com/mig-2g.20gb.engines.copy": "2",
  "nvidia.com/mig-2g.20gb.engines.decoder": "2",
  "nvidia.com/mig-2g.20gb.engines.encoder": "0",
  "nvidia.com/mig-2g.20gb.engines.jpeg": "2",
  "nvidia.com/mig-2g.20gb.engines.ofa": "0",
  "nvidia.com/mig-2g.20gb.memory": "20096",
  ```
* nvidia.com/mig.capable：是否支持 mig。

我们通过修改 helm values.yaml 的方式，在安装或者更新 helm chart 的时候配置 mig 策略（另外官方文档还介绍了通过动态修改 node label 的方式配置 mig，这里不再叙述）。在下面的配置中，有两个 mig 配置：一个是 `all-disabled`，另一个是 `custom-mig` 自定义配置，因为有两个 profile 1g.10gb 及 2g.20gb，因此是 mixed 策略。
```s
migManager:
  config:
    name: custom-mig-config
    create: true
    data: |-
      config.yaml: |-
        version: v1
        mig-configs:
          all-disabled:
            - devices: all
              mig-enabled: false
          custom-mig:
            - devices: [0]
              mig-enabled: true
              mig-devices:
                "1g.10gb": 2
                "2g.20gb": 2
```


在成功配置完 mig 之后，可以在容器中，通过 nvidia-smi 命令查看当前 gpu 配置，如：
```s
GPU 0: NVIDIA H100 80GB HBM3 (UUID: GPU-b4895dbf-9350-2524-a89b-98161ddd9fe4)
  MIG 3g.40gb     Device  0: (UUID: MIG-7089d0f3-293f-58c9-8f8c-5ea666eedbde)
  MIG 2g.20gb     Device  1: (UUID: MIG-56c30729-347f-5dd6-8da0-c3cc59e969e0)
  MIG 1g.10gb     Device  2: (UUID: MIG-9d14fb21-4ae1-546f-a636-011582899c39)
  MIG 1g.10gb     Device  3: (UUID: MIG-0f709664-740c-52b0-ae79-3e4c9ede6d3b)
```

更多配置可以参考 nvidia 官方文档 [GPU Operator with MIG](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-mig.html)，介绍的比较详细。

### 总结
大概介绍了通过 nvidia-device-plugin 的方式利用 gpu 的时候，需要注意的一些点，有些细节没有理清楚，不过相关文档列出来了，后面会在使用过程中，逐步优化和理解 gpu 的使用。