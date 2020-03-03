---
layout:     post
title:      "go delete 阅读笔记"
date:       2020-2-20 23:33:00
author:     "weak old dog"
header-img-credit: false
tags:
    - golang
---

go夜读[go build-in function](https://www.bilibili.com/video/av61786467?from=search&seid=6353307282304593141)的阅读笔记

delete可以从map中删除一组键值对，即使map上已经没有key，这么做也是安全的。另外delete没有返回值。

一边循环map一边删除元素是可以的。

想清空map怎么办？据说delete不会释放空间（delete只是修改了一个标记，底层内存还是被占用的），分析下面代码：
```
```