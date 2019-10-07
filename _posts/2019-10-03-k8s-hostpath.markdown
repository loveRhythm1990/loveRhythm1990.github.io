---
layout:     post
title:      "k8s hostpath实现"
subtitle:   " \"如何跟docker对接\""
date:       2019-10-03 09:22:00
author:     "wm"
header-img-credit: false
tags:
    - k8s
    - kubelet
    - volume
---

## 前言
k8s的`hostPath`可以将host上的一个文件或者目录挂载到容器中，具体是怎么实现的呢？或者说，是调用的docker的什么接口，这里稍微分析总结一下。

hostPath的官方文档为：[https://kubernetes.io/docs/concepts/storage/volumes/#hostpath](https://kubernetes.io/docs/concepts/storage/volumes/#hostpath)


## 关于mount
本以为跟其他volume插件一下，hostpath的mount工作是在`SetUp`方法中做的，结果这个方法什么都没做。
```golang
// SetUp does nothing.
func (b *hostPathMounter) SetUp(mounterArgs volume.MounterArgs) error {
	err := validation.ValidatePathNoBacksteps(b.GetPath())
	if err != nil {
		return fmt.Errorf("invalid HostPath `%s`: %v", b.GetPath(), err)
	}

	if *b.pathType == v1.HostPathUnset {
		return nil
	}
	return checkType(b.GetPath(), b.pathType, b.hu)
}
```

