---
layout:     post
title:      "使用 xfs_quota 实现磁盘容量隔离"
date:       2020-08-20 16:35:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 存储
---

使用 xfs_quota 可以实现磁盘容量隔离，本文介绍一下 xfs_quota 工具的使用

#### 准备
分为未挂载设和已挂载设备两种，对于未挂载设备，在挂载设备的时候指定 prjquota 就可以了，比如：
```s
root@ubuntu:~# mount -o prjquota /dev/loop5 /data/volumes/xfs32m
root@ubuntu:~# mount | grep loop5
/dev/loop5 on /data/volumes/xfs32m type xfs (rw,relatime,attr2,inode64,logbufs=8,logbsize=32k,prjquota) 
```
对于已挂载设备，需要修改`/etc/fstab`，在mount选项上添加`prjquota`（或者`pquota`），并且需要重启机器。

#### 设置
创建目录：
```s
mkdir -p /data/volumes/xfs32m/5m
```
初始化project:
```s
xfs_quota -x -c 'project -s -p /data/volumes/xfs32m/5m 100' /data/volumes/xfs32m
```
设置limit：
```s
sudo xfs_quota -x -c 'limit -p bsoft=5m bhard=5m 100' /data/volumes/xfs32m
```
测试limit，直接使用dd命令创建文件就好了。

查看报告：
```s
$ sudo xfs_quota -x -c 'report -h' /data/volumes/xfs32m

Project quota on /data/volumes/xfs32m (/dev/loop1)
                        Blocks              
Project ID   Used   Soft   Hard Warn/Grace   
---------- --------------------------------- 
#0              0      0      0  00 [------]
#100            0     5M     5M  00 [------]
#200            0    10M    10M  00 [------]
```
#### 删除
查了一下，删除某个 project 是比较困难的，有个 workground 方法:
```s
xfs_quota -x -c 'limit -p bsoft=0 bhard=0 100' /data/volumes/xfs32m
```
然后再删除目录：`/data/volumes/xfs32m/5m`，这个样子在调用 xfs_quota report 的时候应就看不到 projectid 了。
另外有个命令：
```s
$ xfs_quota -x -c "off -up" /data/volumes/xfs32m
$ xfs_quota -x -c "remove -p" /data/volumes/xfs32m
```
这个是删除所有的 project，慎用。

#### 使用配置文件
也可以使用配置文件来配置 xfs quota project
```s
# 挂载
mount -o prjquota /dev/sdb /disks/local-ssd

# 设置id与目标目录的映射
echo 42:/disks/local-ssd/pv0 >> /etc/projects

# 设置名字与id的映射
echo pv0:42 >> /etc/projid

# 初始化Project
xfs_quota -x -c 'project -s pv0' /disks/local-ssd

# 设置limit
xfs_quota -x -c 'limit -p bsoft=1g bhard=1g pv0' /disks/local-ssd
```

我们还可以使用自定义的配置文件，使用-D表示目录映射，使用-P表示id映射，比如：
```s
# 设置id与目标目录的映射
echo 42:/disks/local-ssd/pv0 >> /etc/localcfg/projects

# 设置名字与id的映射
echo pv0:42 >> /etc/localcfg/projid

# 初始化Project
xfs_quota -D /etc/localcfg/projects -P /etc/localcfg/projid -x -c 'project -s pv0' /disks/local-ssd
```

#### 参考
[Linux: Using xfs project quotas to limit capacity within a subdirectory](https://fabianlee.org/2020/01/13/linux-using-xfs-project-quotas-to-limit-capacity-within-a-subdirectory/)

[linux在XFS文件系统上实现针对目录的配额限制](https://blog.csdn.net/weixin_42164528/article/details/96281447)

[磁盘配额quota的应用与实作-鸟叔](https://wizardforcel.gitbooks.io/vbird-linux-basic-4e/content/125.html)

[xfs_quota - ubuntu manpage](http://manpages.ubuntu.com/manpages/bionic/man8/xfs_quota.8.html)
