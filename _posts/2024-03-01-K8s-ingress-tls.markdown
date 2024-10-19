---
layout:     post
title:      "使用 cert-manager 管理 ingress-nginx 证书"
date:       2024-03-01 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - ingress-nginx
---

**文章目录**
- [概述](#概述)
- [环境部署安装](#环境部署安装)
  - [ingress-nginx](#ingress-nginx)
  - [cert-manager](#cert-manager)
- [部署测试应用](#部署测试应用)
  - [部署 deployment 服务](#部署-deployment-服务)
  - [部署 ingress 资源](#部署-ingress-资源)
  - [测试访问](#测试访问)
- [dns 验证](#dns-验证)
  - [http01 验证](#http01-验证)
  - [dns01 验证](#dns01-验证)

### 概述
验证下使用 cert-manager 对 ingress-nginx 证书的 dns 认证、签发、自动 renew。其中认证有两种方式 dns01 认证 以及 http01 认证。

renew 一般不需要我们做什么事情，不过在某些公有云环境下，需要我们将证书上传到 slb 控制台，这种情况下需要程序员自己写代码实现自动化，使用公有云的 sdk。

### 环境部署安装
#### ingress-nginx
使用 helm 安装，下面安装命令使用了自定义的 ingressClass 名字，当一个集群中需要多个 ingress-nginx 的时候，需要这么做。
```s
helm upgrade --install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.ingressClass=nginx-lr90,controller.ingressClassResource.name=nginx-lr90,controller.ingressClassResource.controllerValue=k8s.io/ingress-lr90
```
ingress-nginx 需要公网访问，所以其 service 类型为 loadbalancer。在私有化环境下，可以使用 metallb 分配地址。

#### cert-manager
```s
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.1/cert-manager.yaml
```
部署 letsencrypt staging issuer，staging issuer 是测试环境下用的，签的是非正式证书，但是没有流控限制。触发 letsencrypt 的流控是比较难解的，除了 prod 环境，我们应该总是使用 staging 签证书。

下面 email 需要替换一下，替换成我们个人的或者公司的邮箱，另外我们使用 http01 进行 dns 验证，ingressclass 配置成我们安装 ingress-nginx 时的用的名字。
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: http01-staging
spec:
  acme:
    email: you@email.com
    privateKeySecretRef:
      name: http01-staging-issuer-account-key
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    solvers:
    - http01:
        ingress:
          ingressClassName: nginx-lr90
          serviceType: ClusterIP
```

### 部署测试应用
部署 kuard 服务用于测试。
#### 部署 deployment 服务
参考文档 [Securing NGINX-ingress](https://cert-manager.io/docs/tutorials/acme/nginx-ingress/) 部署一个 deployment 以及 service。

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kuard
spec:
  selector:
    matchLabels:
      app: kuard
  replicas: 1
  template:
    metadata:
      labels:
        app: kuard
    spec:
      containers:
      - image: gcr.io/kuar-demo/kuard-amd64:1
        imagePullPolicy: Always
        name: kuard
        ports:
        - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: kuard
spec:
  ports:
  - port: 80
    targetPort: 8080
    protocol: TCP
  selector:
    app: kuard        
```
#### 部署 ingress 资源
下面的配置有几个需要注意的地方：
* 使用 `letsencrypt-staging` issuer，也就是我们上面创建的 issuer。
* 使用 `nginx-lr90` 这个 ingressClass。
* 配置 kuard 服务的 virtual host 为 `kuard.cn-dev.mmoo.tech`，我们要使用这个 vhost 访问我们的服务。这个域名需要提前在公有云 dns 服务器处配置好，否则 dns http01 无法验证。
* 转发规则部分就是根据 host `kuard.cn-dev.mmoo.tech` 转发到同命名空间的 service `kuard`。

```yaml
root@mo-next-master0:~/lr90# cat ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: kuard
  namespace: lr90
  annotations:
    cert-manager.io/cluster-issuer: "http01-staging"
spec:
  ingressClassName: nginx-lr90
  tls:
  - hosts:
    - kuard.mo.cn-dev.mmoo.tech
    secretName: kuard-tls
  rules:
  - host: kuard.mo.cn-dev.mmoo.tech
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: kuard
            port:
              number: 80
```
创建完成之后，cert-manager 会自动帮我们创建 certificate，并处理后续证书的 renew，自动创建的证书名称跟上面的 secret `kuard-tls` 一致。

#### 测试访问
使用 curl 命令进行测试。需要使用 `-L` 处理 `308 Permanent Redirect` 重定向，以及 `-k` 不验证证书，因为是 staging 环境签的证书。
```s
lr90@sj pulumi % curl -L -k kuard.mo.cn-dev.mmoo.tech
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">

  <title>KUAR Demo</title>

  <link rel="stylesheet" href="/static/css/bootstrap.min.css">
  <link rel="stylesheet" href="/static/css/styles.css">
  ... ...
```

### dns 验证
dns 验证是 ca 验证**签证书的人真正拥有证书里面的域名**，或者说对域名具有控制权，有下面两种方式。
#### http01 验证
http01 需要结合 ingress 使用，在使用这种方式时，需要事先在 dns 服务器处添加好 ingress 资源中的域名，比如上面的 `kuard.mo.cn-dev.mmoo.tech`，cert-manager 在帮我们执行 http01 验证的时候，会在对应 ingress 资源同命名空间创建一个用于验证域名的 http 服务以及对应的临时 ingress。这个临时 
+9q3.*6    ingress 的配置大概如下：
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cm-acme-http-solver-<random-suffix>  # 动态生成的名字，带有随机后缀
  namespace: <your-namespace>                # Ingress 所在的命名空间
spec:
  rules:
  - host: example.com                                  # 待验证的域名
    http:
      paths:
      - path: /.well-known/acme-challenge/<token>      # ACME 验证路径
        pathType: Exact
        backend:
          service:
            name: cm-acme-http-solver-<random-suffix>  # 指向 cert-manager 创建的内部服务
            port:
              number: 8089                             # cert-manager 服务监听的端口

```
上面的临时 ingress 以及整个 http01 验证我们都不需要关心，都是自动完成的。进行 dns 验证时，只要能访问通这个地址 `kuard.mo.cn-dev.mmoo.tech/.well-known/acme-challenge/<token>` 就认为验证是成功的。

#### dns01 验证
dns01 验证是指需要域名拥有者在域名服务出添加一个 txt 记录，证书提供商监听到这个 txt 记录之后，就认为域名是合法的。
参考 [cert-manager webhook-example](https://github.com/cert-manager/webhook-example) 实现 webhook 即可。总体来说就是实现下面几个接口，就是添加 txt 记录和删除 txt 记录，对于阿里云来说，就是需要我们调用阿里云的 dns sdk 去添加 txt 记录。
```go
// Solver has the functionality to solve ACME challenges. This interface is
// implemented internally by RFC2136 DNS provider and by external webhook solver
// implementations see https://github.com/cert-manager/webhook-example
type Solver interface {
	// Name is the name of this ACME solver as part of the API group.
	// This must match what you configure in the ACME Issuer's DNS01 config.
	Name() string

	// Present should 'present' the ACME challenge solving parameters as
	// defined in the given challenge resource.
	// TODO: add notes about duplicate records with DNS01
	Present(ch *whapi.ChallengeRequest) error

	// CleanUp should remove any presented challenge records for the given
	// challenge resource
	// TODO: add notes about duplicate records with DNS01
	CleanUp(ch *whapi.ChallengeRequest) error

	// Initialize is called as a post-start hook when the apiserver starts.
	// https://github.com/kubernetes/apiserver/blob/release-1.26/pkg/server/hooks.go#L32-L42
	Initialize(kubeClientConfig *restclient.Config, stopCh <-chan struct{}) error
}
```