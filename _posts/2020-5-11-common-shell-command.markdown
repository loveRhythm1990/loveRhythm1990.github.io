---
layout:     post
title:      "常用shell命令"
date:       2020-05-11 18:38:00
author:     "weak old dog"
header-img-credit: false
tags:
    - linux
---

工作中经常涉及一些运维操作，一些shell命令用过了就会忘，以后有shell命令都会放在这里。

###### 求和、求平均值
比较常见的情况是由下面文件（或者其他命令的输出），需要求第二列的sum与平均值
```s
jucheng@JM sample % cat testfile
c1 c2
abc 2323
efd 332
ghi 22
```
求和的方式比较简单，`NR!=1`表示过滤掉第一行，NR就是行号,`$2`表示第二列，`END`表示所有的行遍历结束后执行的语句，必须要大写，awk语句要放在单引号里面。
```s
jucheng@JM sample % cat testfile | awk 'NR!=1 {sum+=$2} END{print sum/NR}'
2677
```
求平均数，这个地方要除以`NR-1`是因为第一行我们不算。
```s
jucheng@JM sample % cat testfile | awk 'NR!=1 {sum+=$2} END{print sum/(NR-1)}'
892.333
```

###### 参考
[AWK 简明教程](https://coolshell.cn/articles/9070.html)
