---
layout:     post
title:      "k8s调度器分析"
subtitle:   " \"part1-调度pod过程\""
date:       2020-1-14 10:46:00
author:     "weak old dog"
header-img-credit: false
tags:
	- k8s
	- scheduler
---

关于k8s的调度器，打算写两篇文章，第一篇是概述scheduler调度pod过程，之前零零碎碎介绍了scheduler的一些细节，概述是计划将整个流程串联起来。第二篇是介绍scheduler的构造过程，怎么定制化scheduler等。本文是关于第一篇。