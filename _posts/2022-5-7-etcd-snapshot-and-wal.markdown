---
layout:     post
title:      "Etcd 中 snapshot 以及 wal 实现"
date:       2022-5-7 10:10:00
author:     "decent"
header-img-credit: false
tags:
  - Etcd
---


这部分感觉不复杂，但是应该挺重要的

```s
[root@centos etcd]# ls
lost+found  member
[root@centos etcd]# ls member/
snap  wal
[root@centos etcd]# ls -lh member/snap/
总用量 268M
-rw-r--r-- 1 root root 244K 5月  23 20:35 00000000000006cc-000000004f3232a9.snap
-rw-r--r-- 1 root root 244K 5月  23 20:56 00000000000006cc-000000004f33b94a.snap
-rw-r--r-- 1 root root 244K 5月  23 21:17 00000000000006cc-000000004f353feb.snap
-rw-r--r-- 1 root root 244K 5月  23 21:38 00000000000006cc-000000004f36c68c.snap
-rw-r--r-- 1 root root 244K 5月  23 21:59 00000000000006cc-000000004f384d2d.snap
-rw------- 1 root root 267M 5月  23 22:13 db
[root@centos etcd]# ls -lh member/wal/
总用量 428M
-rw-r--r-- 1 root root 62M 9月  11 2021 0000000000001693-000000000e1e1702.wal.broken
-rw------- 1 root root 62M 5月  23 21:50 000000000000b0ff-000000004f371b30.wal
-rw------- 1 root root 62M 5月  23 21:56 000000000000b100-000000004f3799aa.wal
-rw------- 1 root root 62M 5月  23 22:03 000000000000b101-000000004f381357.wal
-rw------- 1 root root 62M 5月  23 22:09 000000000000b102-000000004f389243.wal
-rw------- 1 root root 62M 5月  23 22:13 000000000000b103-000000004f390bd6.wal
-rw------- 1 root root 62M 5月  23 22:09 0.tmp
```