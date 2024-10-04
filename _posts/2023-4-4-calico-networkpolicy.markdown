---
layout:     post
title:      "使用 Calico 配置容器访问策略 NetworkPolicy"
date:       2023-4-4 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 网络
---

**文章目录**
- [概述](#概述)
- [环境部署](#环境部署)
  - [使用 kind 部署 K8s 集群](#使用-kind-部署-k8s-集群)
  - [部署 calico 插件](#部署-calico-插件)
- [服务测试部署](#服务测试部署)
- [配置 networkpolicy](#配置-networkpolicy)
  - [deny all](#deny-all)
  - [允许 default 命名空间的特定 pod 访问](#允许-default-命名空间的特定-pod-访问)


### 概述
本文通过一个 demo 验证在 calico 网络插件中，通过 K8s networkpolicy 限制 pod 之间的流量，较为简单，意在验证一遍流程。更多关于 networkpolicy 的细节需要参考官方文档。

### 环境部署
#### 使用 kind 部署 K8s 集群
部署单节点 K8s 集群，并通过配置 `disableDefaultCNI: true` 禁用默认的 cni 插件。
```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
  podSubnet: 172.16.0.0/16
```
通过下面命令创建集群。
```s
kind create cluster --config kind.yaml
```

#### 部署 calico 插件
通过 manifest 部署。
```s
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml
```
部署成功后，calico pod 在 kube-system 命名空间。
```s
lr90@sj calico % kubectl get pods -n kube-system
NAME                                         READY   STATUS    RESTARTS        AGE
calico-kube-controllers-658b677684-rqbw7     1/1     Running   8 (9h ago)      20h
calico-node-p5k9c                            1/1     Running   0               20h
```


### 服务测试部署
部署 nginx 服务，部署在 nginx-ns 命名空间，同时部署一个 curl 客户端，部署在 default 命名空间，我们通过给 nginx 配置 networkpolicy 来验证流量规则。
```yaml
apiVersion: v1
kind: Service
metadata:
  name: nginx
  namespace: nginx-ns
  labels:
    app: nginx
spec:
  ports:
  - port: 80
    name: web
  clusterIP: None
  selector:
    app: nginx
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: web
  namespace: nginx-ns
spec:
  selector:
    matchLabels:
      app: nginx # has to match .spec.template.metadata.labels
  serviceName: "nginx"
  replicas: 1
  minReadySeconds: 10 # by default is 0
  template:
    metadata:
      labels:
        app: nginx # has to match .spec.selector.matchLabels
    spec:
      terminationGracePeriodSeconds: 10
      containers:
      - name: nginx
        image: registry.k8s.io/nginx-slim:0.24
        ports:
        - containerPort: 80
          name: web
```
部署 curl 客户端，在 default 命名空间。
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: curl
  labels:
    app: curl
spec:
  containers:
    - command:
        - sleep
        - "3600"
      image: curlimages/curl:latest
      name: main
```
测试连通性，没有问题，此时我们没有创建任何 networkpolicy。
```s
~ $ curl nginx.nginx-ns.svc
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
    body {
        width: 35em;
        margin: 0 auto;
        font-family: Tahoma, Verdana, Arial, sans-serif;
    }
</style>
</head>
```

### 配置 networkpolicy
#### deny all
下面 networkpolicy 作用于 nginx-ns 命名空间带有 app=nginx label 的 pod，并且不允许所有流量流入这些 pod，也不允许所有流量流出。
```yaml
kind: NetworkPolicy
apiVersion: networking.k8s.io/v1
metadata:
  name: nginx-np
  namespace: nginx-ns
spec:
  podSelector:
    matchLabels:
      app: nginx
  policyTypes:
    - Ingress
    - Egress
```
我们通过在 curl pod 中访问 nginx 验证一下，结果为访问不通。
```s
~ $ curl nginx.nginx-ns.svc
curl: (28) Failed to connect to nginx.nginx-ns.svc port 80 after 134817 ms: Could not connect to server
```
#### 允许 default 命名空间的特定 pod 访问
下面更新 networkpolicy 配置，通过 namespaceSelector 以及 podSelector 来选择允许的命名空间以及 pod 集合。
```yaml
kind: NetworkPolicy
apiVersion: networking.k8s.io/v1
metadata:
  name: nginx-np
  namespace: nginx-ns
spec:
  podSelector:
    matchLabels:
      app: nginx
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: curl
          namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: default
```
再次在 curl pod 中访问 nginx servcie，测试访问成功。
```s
~ $ curl nginx.nginx-ns.svc
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
    body {
        width: 35em;
        margin: 0 auto;
        font-family: Tahoma, Verdana, Arial, sans-serif;
    }
</style>
</head>
<body>
```