---
layout:     post
title:      "K8s vertical-pod-autoscaler 垂直扩缩容"
date:       2022-5-1 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 扩缩容
---

- [vpa 概述](#vpa-概述)
	- [四种工作模式](#四种工作模式)
	- [组件与架构](#组件与架构)
- [安装与测试](#安装与测试)
	- [vpa 安装](#vpa-安装)
	- [部署测试 deployment](#部署测试-deployment)
	- [验证扩容效果](#验证扩容效果)
- [参考](#参考)


### vpa 概述
本文研究一下 [vertical-pod-autoscaler](https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler) 的基本工作方式，不涉及原理性的东西，尤其是 recommender 如何推荐资源的，这个有些复杂，希望后面有时间能理解下推荐资源的原理。

#### 四种工作模式
vpa 有四种工作方式，通过 vpa cr 的 `updateMode: Auto` 字段来定义，默认是 Auto 方式。
* Auto: vpa 会在 pod 创建的时候通过 webhook 修改 pod 的资源请求；pod 正在运行时，updater 组件也会把 pod evicted，并在创建时重新配置资源请求。在当前实现中，Auto 的工作模式跟 Recreate 是一致的，不过当原地升级（in-place update）支持之后，推荐使用 Auto 模式。
* Recreate: 如 Auto，当正在运行的 pod 有新的推荐值时，会被 evicted 并重新设置资源 request。
* Initial: 仅仅在 pod 创建的时候修改资源，不会 evicted。
* Off: 不会修改资源请求值，仅仅把推荐值写到 vpa cr 的 status 中，比如
  
  ```yaml
  status:
  conditions:
  - lastTransitionTime: "2022-05-01T09:55:31Z"
    status: "True"
    type: RecommendationProvided
  recommendation:
    containerRecommendations:
    - containerName: hamster
      lowerBound:
        cpu: 577m
        memory: 262144k
      target:
        cpu: 627m
        memory: 262144k
      uncappedTarget:
        cpu: 627m
        memory: 262144k
      upperBound:
        cpu: "1"
        memory: 262144k
  ```

#### 组件与架构
vpa 包含三个组件：Recommender、Updater、Admission Plugin，整体相对比较容易理解：
* Recommender： 根据容器当前和过去的资源使用值，推荐一组资源使用。
* Updater: 检查 pod 当前配置的资源使用量跟 vpa 中的推荐是否一致，如果不一致，则 evict pod。
* Admission Plugin: 当 pod 创建的时候，通过 webHook 修改 pod 的 resource 配置。

vpa 的架构和设计思路可以参考 [vertical-pod-autoscaler.md](https://github.com/kubernetes/design-proposals-archive/blob/main/autoscaling/vertical-pod-autoscaler.md).
![java-javascript](/pics/vpa-architecture.png){:height="50%" width="50%"}

### 安装与测试
首先通过示例看一下 vpa 的安装与使用，走通 happy pass 流程。
#### vpa 安装
vpa 组件以来 metrics-server 提供资源利用指标，在部署 vpa 部署之前需要先部署 metrics-server。将 virtical-pod-autoscaler 项目拷贝下来，通过项目目录下的 vpa-up.sh 脚本安装 vpa。
```s
./hack/vpa-up.sh
```
成功执行之后，将会安装 vpa crd 以及启动 vpa 相关组件。
```s
lr90@sj vertical-pod-autoscaler % kubectl get crd
NAME                                                  CREATED AT
verticalpodautoscalercheckpoints.autoscaling.k8s.io   2022-05-01T09:37:34Z
verticalpodautoscalers.autoscaling.k8s.io             2022-05-01T09:37:34Z

lr90@sj vertical-pod-autoscaler % kubectl get pods -n kube-system | grep vpa
vpa-admission-controller-7c8577fc58-d25sl    1/1     Running   0              95s
vpa-recommender-59c4db8575-l8stv             1/1     Running   0              96s
vpa-updater-6b8fdf7df4-99ngv                 1/1     Running   0              96s
```

#### 部署测试 deployment
通过 vpa 项目中的 hamster（翻译为仓鼠） 进行测试，该 yaml 文件定义了一个 VerticalPodAutoscaler 资源以及 deployment 资源。
```s
kubectl create -f examples/hamster.yaml
```

其中 VerticalPodAutoscaler crd 资源的定义如下:
```yaml
apiVersion: "autoscaling.k8s.io/v1"
kind: VerticalPodAutoscaler
metadata:
  name: hamster-vpa
spec:
  # recommenders: 
  #   - name: 'alternative'
  # updatePolicy:
  #   updateMode: Auto
  targetRef:
    apiVersion: "apps/v1"
    kind: Deployment
    name: hamster
  resourcePolicy:
    containerPolicies:
      - containerName: '*'
        minAllowed:
          cpu: 100m
          memory: 50Mi
        maxAllowed:
          cpu: 1
          memory: 500Mi
        controlledResources: ["cpu", "memory"]
```
vpa cr 资源的部分字段解释如下：
* recommenders: 使用的 recommender 列表，当集群中部署有多个 recommender 的时候，可以选择其中一个或多个使用，一般不同 recommender 有不同的配置参数。可以在 recommender 启动时指定名字以及相关参数。
  ```s
  I0501 09:46:30.085527       1 flags.go:57] FLAG: --recommendation-lower-bound-cpu-percentile="0.5"
  I0501 09:46:30.085530       1 flags.go:57] FLAG: --recommendation-lower-bound-memory-percentile="0.5"
  I0501 09:46:30.085532       1 flags.go:57] FLAG: --recommendation-margin-fraction="0.15"
  I0501 09:46:30.085535       1 flags.go:57] FLAG: --recommendation-upper-bound-cpu-percentile="0.95"
  I0501 09:46:30.085538       1 flags.go:57] FLAG: --recommendation-upper-bound-memory-percentile="0.95"
  I0501 09:46:30.085540       1 flags.go:57] FLAG: --recommender-interval="1m0s"
  I0501 09:46:30.085569       1 flags.go:57] FLAG: --recommender-name="default"
  ``` 
* targetRef: 跟 hpa 一样，指定作用的控制器对象。
* containerName: 指定作用的容器名字，`*` 表示没有专门配置扩容策略的容器，这里可以看作是所有容器。
* minAllowed/maxAllowed: 所能使用资源的区间范围，pod 的资源 limit 不能超过这个值。 
* updatePolicy：工作模式，vpa 一共有四种工作方式：Auto/Recreate/Initial/Off，默认是 Auto 模式。

deployment 测试应用的配置比较简单，其使用的 ubuntu 镜像，所运行的脚本命令如下，含义为：持续运行 `yes` 命令 0.5 秒钟（该命令会持续打印字符 `y` 到标准输出，直到 ctrl-c 或者设置 timeout 值），然后 sleep 0.5 秒钟。该 shell 命令造成的现象就是该容器会使用 0.5 个 cpu，但是在 deployment 的配置中，只给了 0.1 个 cpu，因此预期会触发容器扩容。
```s
while true; do
    timeout 0.5s yes >/dev/null
    sleep 0.5s
done
```

#### 验证扩容效果
首先关注一下扩容之前的配置，包括 deployment 的资源配置，以及 vpa 的资源配置。

首先是 deployment 配置，原始 deployment 的资源配置如下，只配置了 request：
```yaml
        resources:
          requests:
            cpu: 100m
            memory: 50Mi
```
然后是 vpa 的配置，vpa 的配置我们看上一小节的配置，配置了最小和最大配置。

最后我们关注下扩容之后 pod 的资源配置，可以看到资源已经扩容到了 627m，符合我们的预期。
```yaml
    resources:
      requests:
        cpu: 627m
        memory: 262144k
```


### 参考
滴滴写的 [深入理解 VPA Recommender](https://www.infoq.cn/article/z40lmwmtoyvecq6tpoik)

