---
layout:     post
title:      "在 controller runtime 控制器中配置资源 webhook"
date:       2024-03-30 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Operator
---

**目录**
- [controllerruntime webhook 接口概述](#controllerruntime-webhook-接口概述)
- [controller 注册 webhook](#controller-注册-webhook)
- [自动签发证书](#自动签发证书)
- [配置 webhook 不拦截自身 pod](#配置-webhook-不拦截自身-pod)

### controllerruntime webhook 接口概述
K8s 支持 mutatingWebhookConfiguration 以及 validatingWebhookConfiguration 资源，前者主要用于配置一些字段的默认值，或者修改某些字段（比如替换镜像），可用在资源的 create 以及 update 阶段；后者主要是执行一些校验，可以校验资源的 create/update/delete 操作。

在 [kubernetes-sigs/controller-runtime](https://github.com/kubernetes-sigs/controller-runtime) 项目中，资源用户自定以 validator 被定义为 CustomValidator 接口。ValidateDelete 看似没什么用，但是可以阻止特定的资源被删除，比如业务 namespace。用户自定义 defaulter 被定义为 CustomDefaulter 接口。
```go
// 需要被校验的 obj 被以参数的形式传递给校验方法
type CustomValidator interface {
	ValidateCreate(ctx context.Context, obj runtime.Object) (warnings Warnings, err error)
	ValidateUpdate(ctx context.Context, oldObj, newObj runtime.Object) (warnings Warnings, err error)
	ValidateDelete(ctx context.Context, obj runtime.Object) (warnings Warnings, err error)
}

type CustomDefaulter interface {
	Default(ctx context.Context, obj runtime.Object) error
}
```
所以如果我们想实现 MutatingWebhookConfiguration（关注资源的 Create/Update 操作）以及 ValidatingWebhookConfiguration（关注资源的 Create/Update/Delete 操作）只要实现上面四个接口就行，下面具体看下怎么做。

### controller 注册 webhook
kubebuilder 官方文档 [Implementing defaulting/validating webhooks](https://book.kubebuilder.io/cronjob-tutorial/webhook-implementation) 中介绍了通过 kubebuilder 生成 webhook 代码的方式，不过在大多数情况下，我们是直接在代码中注册一个 webhook，并且可以注册任意资源类型的 webhook。

假设下面 PodHook 结构体实现上面四个接口，那我们可以通过下面方式注册 webhook。在初始化 Controller Manager 的时候，指定 webhook server 的监听地址为 8082，这里没有指定 cert 地址，根据 [kubebuilder 文档](https://book.kubebuilder.io/cronjob-tutorial/running) 默认地址为 /tmp/k8s-webhook-server/serving-certs，这意味着我们需要将 certificate 对应的 secret 挂载到这个目录，后面会介绍怎么通过 cert-manager 为webhook自动签发证书。

> 注意，我们在注册地址时，一定要以分隔符 `/` 开头，要不然虽然能注册成功，但是在访问的时候，总是报 404 错误。

```go
mgr, _ := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
    Scheme:             scheme,
    MetricsBindAddress: ":8090",
    WebhookServer: webhook.NewServer(webhook.Options{
        Port: 8082,
    }),
})
ph := &PodHook{}
mgr.GetWebhookServer().Register("/mutating", admission.WithCustomDefaulter(scheme, &corev1.Pod{}, ph))
mgr.GetWebhookServer().Register("/validating", admission.WithCustomValidator(scheme, &corev1.Pod{}, ph))
```

### 自动签发证书
K8s Apiserver 在跟 webhook 通信时，是通过 https 进行的，Apiserver 需要验证 webhook server 证书，因此需要我们提供一个 ca 证书，通过这个证书来验证 webhook server 的cert。我们在 NewServer 的时候，可以手动传入一个证书目录，包含 tls.crt 和 tls.key 两个文件。同时，也必须在 MutatingWebhookConfiguration 中写入一个 caBundle，用来让 Apiserver 验证 tls.crt 以及 tls.key。这个过程非常繁琐，要生成证书，并要 renew 证书。幸好 cert-manager 可以帮我们做这个事情，即通过 cert-manager 自动生成证书。

用 cert-manager 生成证书需要一个本地 pki，具体包括：issuer、certificate。这里用 [SelfSigned](https://cert-manager.io/docs/configuration/selfsigned/) issuer 就可以，certificate 生成的 secret，我们需要挂载到我们的控制器 deployment 中，挂到默认的目录 /tmp/k8s-webhook-server/serving-certs 就可以。对于 MutatingWebhookConfiguration 配置，需要需要一个额外的 annotation 配置，让 cert-manager 自动找到对应的 certificate 并注入 cabundle：`cert-manager.io/inject-ca-from: test/cluster-webhook`，其中 test 是证书的命名空间，cluster-webhook 是证书的名字。

> 关于 selfsigned issuer，默认情况下其签发的证书之间是没有关系的，各个证书是用自己的私钥自签名的，所以叫自签名证书。但是我们可以通过 selfsigned issuer 签发一个 ca certificate（其 isCA 字段为 true），然后再创建一个 selfsigned issuer B，并且在 issuer B 中指定 CA 为 ca certificate。这样就具有一个私有的 ca 了，用 issuer B 签发的证书，就具有相同的 ca。

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata:
  name: cluster-mutating-webhook
  namespace: test
  annotations:
    cert-manager.io/inject-ca-from: test/cluster-webhook
webhooks:
- admissionReviewVersions:
  - v1
  - v1beta1
  clientConfig:
    service:
      name: cluster-webhook
      namespace: test
      path: /clusters/defaulting
  failurePolicy: Fail
  name: mutating.cluster.test
  rules:
  - apiGroups:
    - core.matrixone-cloud
    apiVersions:
    - v1alpha1
    operations:
    - CREATE
    - UPDATE
    resources:
    - clusters
  sideEffects: None

```

另外，访问 webhook 的时候遇到 `failed to call webhook: Post "https://cluster-webhook.test.svc:443/clusters/validating?timeout=10s": EOF` 错误，大概率是程序 panic 了。

### 配置 webhook 不拦截自身 pod
如果 webhook 拦截自身可能会导致 pod 无法启动。这个因为如果 K8s 创建了 MutatingWebhookConfiguration 资源，但是 pod 还没有启动，此时访问这个 pod 的 service 就会失败。因此我们一般会配置 webhook 不拦截自身。这个是通过资源的 `objectSelector` 字段配置的，这个字段通过 label 来选择需要拦截的资源；当使用 NotIn operator 时，也可以配置不拦截的资源。比如下面配置表示不拦截含有 app.kubernetes.io/hook-excluded=test-image-hook 标签的资源。

```yaml
webhooks:
- admissionReviewVersions:
  # ...
  # ...
  sideEffects: None
  timeoutSeconds: 30
  objectSelector:
    matchExpressions:
      - key: app.kubernetes.io/hook-excluded
        operator: NotIn
        values:
          - test-image-hook
```