---
layout:     post
title:      "PV Controller 框架梳理"
date:       2020-11-27 10:10:00
author:     "weak old dog"
header-img-credit: false
tags:
    - Volume
---

#### syncUnboundClaim
1. 寻找可用的 pv，并绑定 pv 和 pvc。
2. 对于设置了`pvc.Spec.VolumeName`字段的 PVC，检查 pv 状态

在动态 bind 的时候发现的问题，

问题一：

1.14版本对于判断一个pvc是不是delayBound，有个地方不是很合适，那就是如果pvc设置了`volume.kubernetes.io/selected-node`annotation之后，会返回false，那集群中如果既存在静态PV，也存在需要动态provisioning的PVC，那么pv controller会将静态pv跟需要动态provision的pvc绑定，pvc的状态就会变成bind，这样就造成问题了。

最新版本在判断是不是delayBound的时候，仅看storageclass的`volumeBindingMode`是不是`waitForFirstConsumer`。

#### syncBoundClaim
这个主要是做检查操作，检查pvc的`claim.Spec.VolumeName`字段，pv是不是还存在，不存在则标记为ClaimLost状态，也就是`Lost`状态。还有就是检查PV是不是跟其他pvc绑定了。

#### syncVolume
sync主要是检查pv以及pvc的状态，如果pvc没有了，就把pv的状态设置为Release。然后根据pv的回收策略来删除pv，这里要看volume插件没有实现跟Provision对应的delete接口，如果没有实现delete接口，则不删除pv，由额外的controller来删除。
```go
// DeletableVolumePlugin is an extended interface of VolumePlugin and is used
// by persistent volumes that want to be deleted from the cluster after their
// release from a PersistentVolumeClaim.
type DeletableVolumePlugin interface {
	VolumePlugin
	// NewDeleter creates a new volume.Deleter which knows how to delete this
	// resource in accordance with the underlying storage provider after the
	// volume's release from a claim
	NewDeleter(spec *Spec) (Deleter, error)
}
```

问题二：
动态provision的pv可能会被删除，这个主要是在极端情况下，pvc的create事件比pv的create事件要晚，这个在同步volume的时候，认为这个pvc已经被删除了，就会把pv的状态设置为`Release`，（设置为Release之后，pv可能会被外部controller回收）。

其实pv controller考虑到了这个事情，它在拿pvc的时候，如果在本地cache中没有发现，它会从etcd再拿一次，直接通过client-go的get方法。但是在判断要不要从etcd拿pvc的时候，额外做了一个`annBoundByController`的判断，如果没有这个annotation就不从etcd拿，这就导致了，动态Provision的pv没有这个annotation，从而被删除了。

根据`annBoundByController`的注释，`It indicates that the binding (PV->PVC or PVC->PV) was installed by the controller.`应该是说，这个pv跟pvc是由controller绑定的，而非由人工绑定的。因此我们在绑定这个pv的时候，应该设置这个annotation，但是K8s最新的代码去除了这个判断，也就是去除了对这个annotation的检查。为考虑兼容性，这里应该也要把K8s代码改成跟社区最新代码一致。

```go 
obj, found, err := ctrl.claims.GetByKey(claimName)
if err != nil {
	return err
}
if !found && metav1.HasAnnotation(volume.ObjectMeta, annBoundByController) {
	// If PV is bound by external PV binder (e.g. kube-scheduler), it's
	// possible on heavy load that corresponding PVC is not synced to
	// controller local cache yet. So we need to double-check PVC in
	//   1) informer cache
	//   2) apiserver if not found in informer cache
	// to make sure we will not reclaim a PV wrongly.
	// Note that only non-released and non-failed volumes will be
	// updated to Released state when PVC does not exist.
	if volume.Status.Phase != v1.VolumeReleased && volume.Status.Phase != v1.VolumeFailed {
		obj, err = ctrl.claimLister.PersistentVolumeClaims(volume.Spec.ClaimRef.Namespace).Get(volume.Spec.ClaimRef.Name)
		if err != nil && !apierrs.IsNotFound(err) {
			return err
		}
		found = !apierrs.IsNotFound(err)
		if !found {
			obj, err = ctrl.kubeClient.CoreV1().PersistentVolumeClaims(volume.Spec.ClaimRef.Namespace).Get(volume.Spec.ClaimRef.Name, metav1.GetOptions{})
			if err != nil && !apierrs.IsNotFound(err) {
				return err
			}
			found = !apierrs.IsNotFound(err)
		}
	}
}
```