---
layout:     post
title:      "当我们谈论 web 框架的时候，我们在谈什么[TODO]"
date:       2023-2-25 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Golang
    - TODO
---

**文章目录**
- [基本使用](#基本使用)
- [参数绑定](#参数绑定)
- [参数校验](#参数校验)
- [中间件](#中间件)
- [路由效率](#路由效率)
  - [数据结构](#数据结构)

[gin](https://github.com/gin-gonic/gin) 的文档地址为 [https://gin-gonic.com/docs/](https://gin-gonic.com/docs/)。

谈论一个 web 框架的时候，我们在谈论什么？

### 基本使用
常用数据结构

### 参数绑定
匹配 url 中的参数，json xml 等格式

### 参数校验

### 中间件
除了 gin 内置的一些中间件，[https://github.com/gin-gonic/contrib](https://github.com/gin-gonic/contrib) 也有一些中间件，可以分析几个作为例子。

### 路由效率

#### 数据结构
httprouter 压缩动态检索树，类似于 trie 树吧