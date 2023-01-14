---
layout:     post
title:      "K8s liveness & readiness probe 简述"
date:       2020-05-30 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---


kubelet使用`liveness probes`来决定什么时候重启一个容器，比如，liveness probe可以检测到一个死锁，应用因为这个死锁不能继续执行了。重启这个应用的容器可以让应用继续提供服务，尽管有bug。

kubelet使用`readiness probes`来知道一个容器什么时候可以接收流量，一个Pod在其所有的container都ready之后才能认为ready，我们可以借助这个信息来知道这个pod可以作为Service的后端了，当Pod不ready的时候，会从Service的load balancers中移除。

kubelet使用`startup probes`来感知什么时候一个容器应用启动，如果配置了startup probes，在startup probes成功之前，它会disable liveness以及readiness检测，以防止这类检测不会跟应用启动冲突，使用startup probes可以防止启动比较慢的应用被kubelet杀掉。

#### 定义liveness命令（command）
许多应用跑着跑着就挂了，只能靠重启来恢复。Kubernetes可以通过`liveness probes`来检测以及修复此类情况。下面是一个Pod的yaml文件。
```yaml
apiVersion: v1
kind: Pod
metadata:
  labels:
    test: liveness
  name: liveness-exec
spec:
  containers:
  - name: liveness
    image: k8s.gcr.io/busybox
    args:
    - /bin/sh
    - -c
    - touch /tmp/healthy; sleep 30; rm -rf /tmp/healthy; sleep 600
    livenessProbe:
      exec:
        command:
        - cat
        - /tmp/healthy
      initialDelaySeconds: 5
      periodSeconds: 5
```
在这个配置文件中，`livenessProbe`说明Kubelet需要每5秒钟在容器中执行`cat /tmp/healthy`。`initialDelaySeconds`说明在执行第一次探测之前需要等五秒钟。在容器中执行命令时，如果命令执行成功，返回0，这个kubelet认为这个容器是健康的，如果命令返回非零值，Kubelet会杀死这个容器并重启。

这个容器的命令是`touch /tmp/healthy; sleep 30; rm -rf /tmp/healthy; sleep 600`，前30秒livenessProbe是正常的，后面探测就失败了，因此会被重启。


#### 定义liveness HTTP请求
另一种探活方式是使用HTTP Get请求。下面是一个例子：
```yaml
apiVersion: v1
kind: Pod
metadata:
  labels:
    test: liveness
  name: liveness-http
spec:
  containers:
  - name: liveness
    image: k8s.gcr.io/liveness
    args:
    - /server
    livenessProbe:
      httpGet:
        path: /healthz
        port: 8080
        httpHeaders:
        - name: Custom-Header
          value: Awesome
      initialDelaySeconds: 3
      periodSeconds: 3
```
上面的探活方式就是每三秒钟发送一个http get请求，还带着一个自定义的Http header，如果路径`/healthz`的handler返回成功，kubelet认为容器健康，否则kill容器，并重启。在下面区间的返回码认为是成功的`[200, 400)`，即昨闭右开。其他返回码认为是失败的。

#### 定义TCP liveness探测
第三种探活方式是使用TCP socket，在这种方式下，kubelet会试图与容器的特定接口建立socket连接。如果连接建立成功，认为容器健康，否则认为容器失败。
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: goproxy
  labels:
    app: goproxy
spec:
  containers:
  - name: goproxy
    image: k8s.gcr.io/goproxy:0.1
    ports:
    - containerPort: 8080
    readinessProbe:
      tcpSocket:
        port: 8080
      initialDelaySeconds: 5
      periodSeconds: 10
    livenessProbe:
      tcpSocket:
        port: 8080
      initialDelaySeconds: 15
      periodSeconds: 20ß
```
从上面看出，TCP探活方式跟HTTP非常类似，这个例子同时使用了readiness以及liveness探测，kubelet会在容器启动5秒后开始第一次readinessProbe，探测的方式是试图连接容器的8080端口。

除了readiness探测，还配置了liveness探测。kubelet会在容器启动的15秒之后开始第一次liveness probe。跟readiness一样，也是试图与容器的8080端口建立tcp连接。如果liveness probe失败，容器会被重启。

#### 使用命名端口（Use a named port）
这里说的是在liveness probe中可以引用在container.port中定义的端口。如下所示：
```go
ports:
- name: liveness-port
  containerPort: 8080
  hostPort: 8080

livenessProbe:
  httpGet:
    path: /healthz
    port: liveness-port
```
注意在ports中定义的端口`liveness-port`中同时指定了`hostPort`，这是一种暴露容器服务的方式，如果指定了hostPort，我们可以使用`hostIp:hostPort`的方式来访问容器的`containerPort`（通过NAT实现），需要注意的是，因为Pod可能被重新调度，这个hostIp是会变的，因此这个值一般不会设置，而是使用默认值，而且，在Pod重新调度到其他节点之后，我们需要重新确认其他节点的IP来访问容器服务。这与service中的NodePort是不同的，在NodePort情况下，我们使用任意集群节点的IP+NodePort都能访问到Service的clusterIp。

更常用的暴露容器服务的方式是使用service，一种有四种service类型：ClusterIP, NodePort, LoadBalancer, 以及ExternalName。具体可以参考k8s文档。 

#### 使用startup probe来保护启动慢的容器
这里说的是，把startup probe的时间设置的较长一些，在startp probe失败之前，是不会进行livenessProbe的。如下面例子：
```yaml
ports:
- name: liveness-port
  containerPort: 8080
  hostPort: 8080

livenessProbe:
  httpGet:
    path: /healthz
    port: liveness-port
  failureThreshold: 1
  periodSeconds: 10

startupProbe:
  httpGet:
    path: /healthz
    port: liveness-port
  failureThreshold: 30
  periodSeconds: 10
```
startupProbe与livenessProbe使用的命令一致，但是startupProbe设置的超时时间为：`30 * 10 = 300s`，在这之前都不会进行livenessProbe，直到成功之后，才会每隔10s进行一次livenessProbe

#### 定义readiness probes
有时候，应用临时不能提供服务。比如：在启动时应用需要加载大量数据或者配置一些文件，或者在启动后需要依赖外部的服务。在这种情况下，应用并不需要重启，但是也不能提供服务。这个时候就需要readiness探测了。~~如果readiness探测失败了，容器就不会通过Kubernetes Service接收服务了，看来，readiness需要配合service使用，这个有时间需要调查一下。~~

在readiness probe失败后，pod condition中的ContainersReady会被设置为False，Ready condition也会被设置为false。


在容器的整个生命周期中，readiness都是工作的。

Readiness探测的配置跟Liveness配置一致，把livenessProbe换成readinessProbe就行了。Readiness的HTTP以及TCP的配置方式跟LivenessProbe一致。他俩可以同时使用。

#### 关键字段的说明

* initialDelaySeconds: 在容器启动后（start probe成功），readiness或者liveness开始的时延，默认是0秒，最小值就是0。
* successThreshold: 最少的连续的成功次数，（连续这么多次成功才认为是成功）。默认是1，也就是说成功一次就认为是成功了
* failureThreshold：当probe失败时，在放弃前重试的次数。对liveness probe来说，这么多次失败后会重启容器。对于readiness来说，这么多次失败后，Pod会被标记为`Unhealthy`，默认是3，最小值是1.

对于TCP探测来说，下面没怎么看懂：`For a TCP probe, the kubelet makes the probe connection at the node, not in the pod, which means that you can not use a service name in the host parameter since the kubelet is unable to resolve it.`


原文：[Configure Liveness, Readiness and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)

关于liveness以及readiness的弊端，下面链接有挺有意思的阐述：[Kubernetes Liveness and Readiness Probes: How to Avoid Shooting Yourself in the Foot](https://blog.colinbreck.com/kubernetes-liveness-and-readiness-probes-how-to-avoid-shooting-yourself-in-the-foot/)