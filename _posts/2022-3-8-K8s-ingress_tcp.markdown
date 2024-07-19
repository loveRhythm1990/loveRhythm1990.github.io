---
layout:     post
title:      "通过 K8s ingress-nginx 实现 tcp 四层转发"
date:       2022-3-8 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - K8s
---

**文章目录**
- [概述](#概述)
- [部署 mysql](#部署-mysql)
- [Ingress 部署/配置](#ingress-部署配置)
  - [配置 tcp-services configmap](#配置-tcp-services-configmap)
  - [配置 ingress-nginx service](#配置-ingress-nginx-service)
- [测试端口转发](#测试端口转发)


## 概述
K8s Ingress 资源本身不支持四层转发，但是 [ingress-nginx](https://kubernetes.github.io/ingress-nginx/deploy/) 控制器可通过配置端口转发做到 tcp 四层转发，本文以部署 mysql server 为例对四层转发做一下实践。

本文的测试环境为在 mac 上通过 [kind](https://kind.sigs.k8s.io/) 部署一套 K8s 集群。

## 部署 mysql
mysql 通过 helm chart 部署，mysql 镜像有可能拉不下来，需要时配置下代理就好了。部署成功后 root 的密码放在对应命名空间的 secret 中。
```s
helm install my-release oci://registry-1.docker.io/bitnamicharts/mysql
```
部署完成后，需要看一下 mysql 部署的 service，这个 service 一会需要配置在 ingress 的 tcp-services cm 配置文件中。
```s
lr90@sj ~ % kubectl get service my-release-mysql
NAME                        TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
my-release-mysql            ClusterIP   10.96.117.53   <none>        3306/TCP   74m
```

## Ingress 部署/配置
ingress 通过 helm chart 部署。其他部署方式可以参考 [ingress 文档](https://kubernetes.github.io/ingress-nginx/deploy/)。
```s
helm upgrade --install ingress-nginx ingress-nginx --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace
```
为了支持 tcp 四层转发，ingress-nginx 的启动参数需要带有 `--tcp-services-configmap=ingress-nginx/tcp-services` 配置。参数具体含义是配置 tcp 转发的 configmap 的命名空间与名字。如果是通过上面 helm 命令安装的，可以先手动修改下 deployment 启动参数，这里参数最好在 helm 启动的时候就配置好。
```yaml
containers:
- args:
  - /nginx-ingress-controller
  - --tcp-services-configmap=ingress-nginx/tcp-services
```

### 配置 tcp-services configmap
该配置文件 configmap 的命名空间与名字需要与命令行参数中指定的一致。在下面配置中，我们将 3306 端口转发到 default 命名空间的 my-release-mysql service，且转发的 service 的端口为 3306，也就是 mysql server 的端口。
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tcp-services
  namespace: ingress-nginx
data:
  3306: "default/my-release-mysql:3306"
```

### 配置 ingress-nginx service
通过 ingress-nginx 实现四层转发的不足之处就是每部署应用就要修改一下 ingress-nginx 的 service。通过命令 `kubectl edit service -n ingress-nginx ingress-nginx-controller` 修改，在 ports 字段额外添加一个端口，配置该端口后，会自动分配一个 nodeport，我们暂时不需要关注 nodeport。（这里如果不看的代码仅凭猜测的话，其原理应该是 ingress 会监听在 nodeport 端口，并将流量转发到我们的 mysql service）.
```yaml
spec:
  ports:
  - appProtocol: tcp
    name: mysql-server
    port: 3306
    protocol: TCP
    targetPort: 3306
```

## 测试端口转发
将 ingress-nginx service 的 3306 通过端口转发暴露出来。
```s
kubectl port-forward service/ingress-nginx-controller -n ingress-nginx 3306:3306
```
通过 mysql 客户端访问。
```s
lr90@sj ~ % mysql -h127.0.0.1 -uroot -psUlEQhUyt0 -P3306
mysql: [Warning] Using a password on the command line interface can be insecure.
Welcome to the MySQL monitor.  Commands end with ; or \g.
Your MySQL connection id is 1277
Server version: 8.4.1 Source distribution

Copyright (c) 2000, 2023, Oracle and/or its affiliates.

Oracle is a registered trademark of Oracle Corporation and/or its
affiliates. Other names may be trademarks of their respective
owners.

Type 'help;' or '\h' for help. Type '\c' to clear the current input statement.

mysql>
```

测试完成。