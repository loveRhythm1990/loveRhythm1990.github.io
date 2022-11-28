---
layout:     post
title:      "一些 helm 最佳实践"
date:       2022-11-27 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - k8s
    - 运维
---

helm 在 K8s 中用的非常多了，最近也在关注这块，总结一些最佳实践。《Helm 学习指南-Kubernetes上的应用程序管理》这本书前前后后翻了很多次，也算是读完了。另外还买了一本《Harbor权威指南：容器镜像、Helm Chart等云原生制品的管理与实践》后面再看下，学习 helm 是想能掌握 K8s 的基本生态。

### 在 helm install 中指定 namespace
当安装一个组件但是组件所需要的 namespace 不存在时，会报错：Error: namespaces "mcloud-ke2e" not found。此时可以使用 `--create-namespace` 标志可以创建不存在的 namespace。

当集群中已有 namespace 时，再使用 `--create-namespace` 标志不会报错。

使用 `helm uninstall` 卸载组件时，不会卸载 namespace，需要使用 `kubectl delete ns` 命令才能将 ns 删除，这是 helm 的一种保护措施 

> 建议在使用 `helm upgrade --install` 安装组件时，总是要指定 namespace，而不是安装在 default namespace 下面。

### 在 helm chart 中使用 Release.namespace
通过将组件部署的 Release namespace，可以在安装组件时决定其资源安装的 namespace（而不是在编写 chart 的时候），这种情况在同一个组件会被部署多次时候非常有用，如果同一个组件会被部署多次，最佳实践是将其部署到多个 namespace，通过 namespace 提供的隔离机制来实现组件隔离。

将组件部署到 Release namespace 需要在 chart 中引用 Release.Namespace，这样在通过  `helm install -n {namespace}` 部署组件时，组件实际部署到的 ns 就是 helm 命令中指定的 ns。在 chart 配置 `{{ .Release.Namespace }}`之后，不需要做额外其他配置。例子如下：
```s
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "mcloud-ke2e.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
  {{- include "mcloud-ke2e.labels" . | nindent 4 }}
spec:
  backoffLimit: 0
  parallelism: 1
```

### 使用 appVersion 作为应用版本
在一些情况下，我们需要知道应用的镜像版本，而不仅仅是 chart 的版本（chart 的版本并不能反应应用的版本）。在 chart.yaml 文件中，除了可以配置 version，还可以配置 appVersion， 表示chart中应用的版本号。其中 appVersion 可以是非语义化的版本号，如下面的"dev-1.16"。
```s
decent@Mac ~/g/s/s/x/m/a/n/v0.1.0> helm list -A                                                                                                                                                                                1 feature/mcloud-ke2e
NAME  	NAMESPACE 	REVISION	UPDATED                             	STATUS  	CHART               	APP VERSION
my-e2e	mcloud-e2e	2       	2022-11-24 10:54:20.26942 +0800 CST 	deployed	mcloud-ke2e-0.1.1   	dev-1.16   
nacos 	default   	1       	2022-11-23 16:05:33.891095 +0800 CST	deployed	nacos-operator-0.1.2	1.16.0    
```

### 使用 chart 来管理依赖
chart 有两种方式管理依赖，一种是通过在 chart.yaml 文件中通过 dependencies 字段指定依赖，这种是自动管理依赖；另一种是在 chart 的目录 charts 中手动管理依赖。
#### 自动管理
自动管理的方式为，可以参考 [bitnami wordpress](https://github.com/bitnami/charts/blob/main/bitnami/wordpress/Chart.yaml)，这种方式下，这种方式比较简单方便，但是缺少对依赖的动态配置，可配置性较弱。 
```yml
dependencies:
  - name: apache
    version: 1.2.3
    repository: https://example.com/charts
  - name: mysql
    version: 3.2.1
    repository: https://another.example.com/charts
```

#### 手动管理
手动管理是在 charts 目录下添加依赖 chart，假设目前有一个 wordpress chart，其依赖 apache 以及 mysql 两个 chart，可以直接将 apache 以及 mysql 放在 charts 目录下面。
```yml
wordpress:
  Chart.yaml
  # ...
  charts/
    apache/
      Chart.yaml
      values.yaml
      # ...
    mysql/
      Chart.yaml
      values.yaml
      # ...
```
在这种情况下，subchart 的配置可以直接在 values.yaml 中配置，如果我们修改了 subchart 的 values.yaml 文件，使用 `helm upgrade --install` 命令会同步更新 subchart 中的组件。我们用一个官方文档中例子来说明 chart 及其 subchart 的更新。假设 chart A 会创建三个资源：A-Namespace、A-StatefulSet、A-Service，其依赖的 subchart B 会创建三个资源：B-Namespace、B-ReplicaSet、B-Service。则使用 helm install/upgrade 命令安装 chart A 时，资源的安装/升级顺序为：
1. A-Namespace
2. B-Namespace
3. A-Service
4. B-Service
5. B-ReplicaSet
6. A-StatefulSet 

### 使用 hook 来解决前置依赖或者后置检查
helm 提供了 hook 允许我们安装过程中进行干预，在配置了 hook 的 chart 中，其执行流程如下。
![java-javascript](/img/in-post/all-in-one/2022-11-28-17-48-10.png){:height="50%" width="50%"}

一个 hook 就是一个 job，不过配置了特定的 annotation，比如下面的 `"helm.sh/hook": post-install`，如果配置了 annotation，则此 job 不会被认为是 chart 的资源的一部分。根据 annotation 的 value，此 job 将会在特定时间被调用。
```yml
apiVersion: batch/v1
kind: Job
metadata:
  name: "post-install-job"
  namespace: {{ .Release.Namespace }}
  annotations:
    # This is what defines this resource as a hook. Without this line, the job is considered part of the release.
    "helm.sh/hook": post-install
    "helm.sh/hook-delete-policy": hook-succeeded
spec:
  template:
    metadata:
      name: "{{ .Release.Name }}"
      labels:
        app.kubernetes.io/instance: {{ .Release.Name | quote }}
        helm.sh/chart: "{{ .Chart.Name }}-{{ .Chart.Version }}"
    spec:
      restartPolicy: Never
      containers:
        - name: post-install-job
          image: "busybox:latest"
          command:
            - sleep
            - "10"
```

### 使用 helm test 来对应用进行测试
在 chart template 目录中，有一个 tests 目录，可以用来放置对组件的测试任务，该任务需要添加 annotation helm.sh/hook: test，通过 helm test 来对组件进行测试语义更清晰一些。tests 目录下的测试任务需要通过 helm test 命令来触发。

### 将 crd 放在 crds 目录
放在 crds 目录后，helm 会将 crd 视为特殊的资源，具体有以下特征:
1. crd 文件必须是文本，不能是 template
2. helm 在安装资源时，首先安装 crd 资源，等待 crd 安装完成后才安装其他资源，（因为其他资源可能依赖这个 crd）
3. crd 不会被重新安装，无论是调用 helm upgrade 还是 helm roolback，就是要安装新版本 crd 也行。
4. crd 从来不会被删除，因为删除 crd 会删除 crd 所有的实例，即所有的 cr。（卸载 crd 是危险操作，所有的 cr 都会被删除）

参考：

[https://codersociety.com/blog/articles/helm-best-practices](https://codersociety.com/blog/articles/helm-best-practices)

官方文档