---
layout:     post
title:      "关于InClusterConfig的一些知识"
date:       2020-09-20 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - k8s
---

我们在容器中，一般使用InClusterConfig来构造Kubernetes ClientSet，并访问K8s Apiserver。使用示例如下：
```go
	// "k8s.io/client-go/kubernetes"
	// "k8s.io/client-go/rest"
	config, err := rest.InClusterConfig()
	if err != nil {
		klog.Fatalf("Failed to create config: %v", err)
	}
	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		klog.Fatalf("Failed to create client: %v", err)
	}
```

那工作原理是什么呢？都涉及到哪些知识点。InClusterConfig的代码路径为`k8s.io/client-go/rest/config.go`，函数代码并不复杂：
```go
func InClusterConfig() (*Config, error) {
	const (
		tokenFile  = "/var/run/secrets/kubernetes.io/serviceaccount/token"
		rootCAFile = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
	)
	host, port := os.Getenv("KUBERNETES_SERVICE_HOST"), os.Getenv("KUBERNETES_SERVICE_PORT")
	if len(host) == 0 || len(port) == 0 {
		return nil, ErrNotInCluster
	}

	token, err := ioutil.ReadFile(tokenFile)
	if err != nil {
		return nil, err
	}

	tlsClientConfig := TLSClientConfig{}

	if _, err := certutil.NewPool(rootCAFile); err != nil {
		klog.Errorf("Expected to load root CA config from %s, but got err: %v", rootCAFile, err)
	} else {
		tlsClientConfig.CAFile = rootCAFile
	}

	return &Config{
		// TODO: switch to using cluster DNS.
		Host:            "https://" + net.JoinHostPort(host, port),
		TLSClientConfig: tlsClientConfig,
		BearerToken:     string(token),
		BearerTokenFile: tokenFile,
	}, nil
}
```

首先是在`/var/run/secrets/kubernetes.io/serviceaccount/`路径下查找两个文件`token`、`ca.crt`，这个路径是secret的默认挂载路径，我们一般没有主动创建secret，怎么会有这些文件呢？这里其实经历了两步：创建secret以及挂载secret。

##### secret
**创建secret**：这个是K8s Controller Manager中的ServiceAccountController做的，ServiceAccountController中有一个`TokensController`，其有个方法`ensureReferencedToken`，就是为ServiceAccount生成secret的，生成secret包含三个field：
token、ca.crt、namespace。生成secret之后还有将此secret更新到ServiceAccount中去。

以`github.com/kubernetes-sigs/sig-storage-local-static-provisioner`项目为例，我们创建ServiceAccount时，它长这个样子：
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: local-storage-admin
  namespace: default
```
等ServiceAccount创建好之后，它变成了下面这个样子：
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  creationTimestamp: "2020-09-18T09:39:22Z"
  name: local-storage-admin
  namespace: default
  resourceVersion: "732"
  uid: 7d438765-46af-4fc4-b90b-1d0b8bf1c117
secrets:
- name: local-storage-admin-token-2gs2n
```
下面引用的secret就是sa controller给创建的。

**挂载secret**：这个是webhook做的。参考代码路径为：`k8s.io/kubernetes/plugin/pkg/admission/serviceaccount/admission.go`

##### service注入
还是就是读取两个环境变量：`KUBERNETES_SERVICE_HOST`、`KUBERNETES_SERVICE_PORT`，这个是`kubernetes`这个service的ClusterIP以及端口：
```s
decent@ubuntu:~/yamls$ kubectl get service
NAME         TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)   AGE
kubernetes   ClusterIP   10.96.0.1    <none>        443/TCP   4h40m
```
通过service通过环境变量的方式注入到容器中，是kubernete暴露service的一种方式，另一种方式是通过DNS查询的方式，每个service以及Pod都有自己的A记录格式，在创建Pod的时候，可以指定DNS服务器的地址（也就是core dns的service的clusterIP），通过coredns来解析service的A记录。

`kubernetes`这个service是kubelet无条件注入到每个pod中的，一般情况下，只有在同一个namespace中的service才会注入到pod。

##### 权限控制
还是以项目[sig-storage-local-static-provisioner](https://github.com/kubernetes-sigs/sig-storage-local-static-provisioner)为例，通过创建ClusterRole（因为是集群范围的），并将此ClusterRole与ServiceAccount绑定，然后在pod template里指定ServiceAccount名字。相关资源如下：
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: local-storage-provisioner-pv-binding
  namespace: default
subjects:
- kind: ServiceAccount
  name: local-storage-admin
  namespace: default
roleRef:
  kind: ClusterRole
  name: system:persistent-volume-provisioner
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: local-storage-provisioner-node-clusterrole
  namespace: default
rules:
- apiGroups: [""]
  resources: ["nodes"]
  verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: local-storage-provisioner-node-binding
  namespace: default
subjects:
- kind: ServiceAccount
  name: local-storage-admin
  namespace: default
roleRef:
  kind: ClusterRole
  name: local-storage-provisioner-node-clusterrole
  apiGroup: rbac.authorization.k8s.io
```
上面定义了两个ClusterRoleBinding，将同一个ServiceAccount和两个ClusterRole绑定在了一起，一个是在这里定义的，另一个是系统默认配置的`system:persistent-volume-provisioner`.


还有就是K8s认证的一些东西，这里涉及到两个文件，一个是token，一个是CA，这个涉及到东西比较多，有时间再组织下。