---
layout:     post
title:      "K8s Scheduler 及 Apiserver 是如何处理 binding 请求的"
date:       2021-1-2 17:42:00
author:     "weak old dog"
header-img-credit: false
tags:
    - K8s
---

研究一下k8s是如何处理pod的binding请求的，如何将pod与node绑定的
#### scheduler
scheduler发起binding请求：
```go
// 构造一个binding资源
err := sched.bind(assumedPod, &v1.Binding{
    // 注意Binding资源中，name是pod的name，namespace，以及uid也是pod的
    ObjectMeta: metav1.ObjectMeta{Namespace: assumedPod.Namespace, Name: assumedPod.Name, UID: assumedPod.UID},
    Target: v1.ObjectReference{
        Kind: "Node",
        Name: scheduleResult.SuggestedHost,
    },
})
```
这个bind请求，最终转化为client-go的请求：
```go
// 调用client-go的请求
func (b *binder) Bind(binding *v1.Binding) error {
	klog.V(3).Infof("Attempting to bind %v to %v", binding.Name, binding.Target.Name)
	return b.Client.CoreV1().Pods(binding.Namespace).Bind(binding)
}

// 下面是client-go中对Bind请求的处理，调用的是rest请求，转化为对subResource binding的处理
// Bind applies the provided binding to the named pod in the current namespace (binding.Namespace is ignored).
func (c *pods) Bind(binding *v1.Binding) error {
	return c.client.Post().Namespace(c.ns).Resource("pods").Name(binding.Name).SubResource("binding").Body(binding).Do().Error()
}
```

#### apiserver
看下apiserver是如何处理这个Bind请求的，根据下面注册的路由，查看binding请求是如何处理的，route注册在下面方法中：

`func (c LegacyRESTStorageProvider) NewLegacyRESTStorage(restOptionsGetter generic.RESTOptionsGetter) (LegacyRESTStorage, genericapiserver.APIGroupInfo, error) `

```go
	restStorageMap := map[string]rest.Storage{
		"pods":             podStorage.Pod,
		"pods/attach":      podStorage.Attach,
		"pods/status":      podStorage.Status,
		"pods/log":         podStorage.Log,
		"pods/exec":        podStorage.Exec,
		"pods/portforward": podStorage.PortForward,
		"pods/proxy":       podStorage.Proxy,
        "pods/binding":     podStorage.Binding, // 处理binding请求
        // 忽略其他路由
    }
```
binding的所有操作都是`BindingRest`对象执行的。看下create方法。
```go
func (r *BindingREST) Create(ctx context.Context, obj runtime.Object, createValidation rest.ValidateObjectFunc, options *metav1.CreateOptions) (out runtime.Object, err error) {
	binding := obj.(*api.Binding)
	err = r.assignPod(ctx, binding.Name, binding.Target.Name, binding.Annotations, dryrun.IsDryRun(options.DryRun))
	out = &metav1.Status{Status: metav1.StatusSuccess}
	return
}
```
调用了`assignPod`方法，后者又调用了`setPodHostAndAnnotations`，注意参数`oldMachine`初始为空，代码如下：
```go
// setPodHostAndAnnotations sets the given pod's host to 'machine' if and only if it was
// previously 'oldMachine' and merges the provided annotations with those of the pod.
// Returns the current state of the pod, or an error.
func (r *BindingREST) setPodHostAndAnnotations(ctx context.Context, podID, oldMachine, machine string, annotations map[string]string, dryRun bool) (finalPod *api.Pod, err error) {
	podKey, err := r.store.KeyFunc(ctx, podID)
	if err != nil {
		return nil, err
    }
    // 调用GuaranteedUpdate
	err = r.store.Storage.GuaranteedUpdate(ctx, podKey, &api.Pod{}, false/*ignoreNotFound*/, nil/*preconditions*/, storage.SimpleUpdate(func(obj runtime.Object) (runtime.Object, error) {
		pod, ok := obj.(*api.Pod)
		if !ok {
			return nil, fmt.Errorf("unexpected object: %#v", obj)
		}
		if pod.DeletionTimestamp != nil {
			return nil, fmt.Errorf("pod %s is being deleted, cannot be assigned to a host", pod.Name)
        }
        // 如果已经存在NodeName且跟初始不一致，则报错
		if pod.Spec.NodeName != oldMachine {
			return nil, fmt.Errorf("pod %v is already assigned to node %q", pod.Name, pod.Spec.NodeName)
        }
        // 更新
		pod.Spec.NodeName = machine
		if pod.Annotations == nil {
			pod.Annotations = make(map[string]string)
		}
		for k, v := range annotations {
			pod.Annotations[k] = v
        }
        // 更新Pod Scheduled condition
		podutil.UpdatePodCondition(&pod.Status, &api.PodCondition{
			Type:   api.PodScheduled,
			Status: api.ConditionTrue,
		})
		finalPod = pod
		return pod, nil
	}), dryRun)
	return finalPod, err
}
```
`GuaranteedUpdate`的实现中有个无限循环，在一次循环中获取最新的Object，然后对其调用updateFunc，然后更新到etcd，如果发现conflict，则重新下一个循环。个人猜想，把对pod.Spec.nodename的修改采用binding resource的形式，就是简化客户端代码的编写，把这部分保证更新的逻辑放到apiserver这边。