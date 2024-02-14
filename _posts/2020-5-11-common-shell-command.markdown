---
layout:     post
title:      "shell 运维命令集合"
date:       2020-05-11 18:38:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 运维
---

**目录**
- [求和、求平均值](#求和求平均值)
- [grep 显示前后命令](#grep-显示前后命令)
- [循环读取文件的内容](#循环读取文件的内容)
- [查看磁盘读写io](#查看磁盘读写io)
- [nc 网络测试命令](#nc-网络测试命令)
- [变量的默认值](#变量的默认值)
- [安装包管理](#安装包管理)
- [sed 命令](#sed-命令)
- [ansible 基本用法](#ansible-基本用法)
- [nslookup 测试域名解析](#nslookup-测试域名解析)
- [ssh 配置免密登录](#ssh-配置免密登录)
- [使用 tc 模拟网络丢包和时延](#使用-tc-模拟网络丢包和时延)
- [vi 复制与删除](#vi-复制与删除)
- [查看文件被哪个进程占用](#查看文件被哪个进程占用)
- [set -euxo pipefail](#set--euxo-pipefail)
- [Mac as Proxy](#mac-as-proxy)
- [参考](#参考)

工作中经常涉及一些运维操作，一些shell命令用过了就会忘，以后有shell命令都会放在这里。

###### 求和、求平均值
比较常见的情况是由下面文件（或者其他命令的输出），需要求第二列的sum与平均值
```s
j@JM sample % cat testfile
c1 c2
abc 2323
efd 332
ghi 22
```
求和的方式比较简单，`NR!=1`表示过滤掉第一行，NR就是行号,`$2`表示第二列，`END`表示所有的行遍历结束后执行的语句，必须要大写，awk语句要放在单引号里面。
```s
j@JM sample % cat testfile | awk 'NR!=1 {sum+=$2} END{print sum/NR}'
2677
```
求平均数，这个地方要除以`NR-1`是因为第一行我们不算。
```s
j@JM sample % cat testfile | awk 'NR!=1 {sum+=$2} END{print sum/(NR-1)}'
892.333
```
###### grep 显示前后命令
```s
grep -C 5 foo file #显示file文件里匹配foo字串那行以及上下5行
grep -B 5 foo file #显示foo及前5行
grep -A 5 foo file #显示foo及后5行

#另外，grep使用双引号表示不对特殊字符进行转义。
#"或" 操作，使用正则表达式，下面是过滤出含有a或者b的行
grep -E "a|b"
```
###### 循环读取文件的内容
下面是循环读取文件的内容，但是是以空格为分隔符的。
```s
#! /bin/bash

for line in $(cat files)
do
        rm -rf $line
done
```
上面命令可以写成一行：`for line in $(cat files); do echo ${line}; done`，如果要以行为分隔符，可以这么写
```s
#!/bin/bash
cat files | xargs -d "\n" echo
```
xargs 用来将前面命令的输出作为后面命令的输入，`-d "\n"`表示以空格为分隔符将输入分割，并把分割后的每一个字符串作为输入传递给后面的命令。xargs 的命令格式为`xargs [-options] [command]`，详细使用方式可以参考[xargs 命令教程](https://ruanyifeng.com/blog/2019/08/xargs-tutorial.html)

###### 查看磁盘读写io
```s
iostat -d -m -x 1 10000
```
* -d: 只看设备
* -m: 以MB为单位
* -x: 显示扩展数据
* 1：每秒打印一次
* 10000： 一共打印10000次

这部分参考：[Linux IO监控与深入分析](https://jaminzhang.github.io/os/Linux-IO-Monitoring-and-Deep-Analysis/)

查看进程写IO，-d: 只显示IO
```s
pidstat -d 1
```

###### nc 网络测试命令
```s
# 安装 nc
yum install nc
# 在1234端口起一个tcp服务
nc -l 1234
# 往某个主机的端口发送数据
echo -e '{"method":"HelloService.Hello","params":["hello"],"id":1}' | nc localhost 1234

# 使用 nc 测试网络通不通
nc -v 172.16.xx.xx 31973
# 输出如下
#Ncat: Version 7.50 ( https://nmap.org/ncat )
#Ncat: Connected to 172.16.xx.xx:31973.
#^C

# 使用 telnet 测试网络通不通
telnet 172.16.xx.xx 31973
# 输出如下
#Trying 172.16.16.19...
#Connected to 172.16.16.19.
#Escape character is '^]'.
#^CConnection closed by foreign host.
```

###### 变量的默认值
如果变量没有定义，或者为空串，则使用默认值：
```s
a=${a:-default-value}
echo ${a}
# 输出：default-value
```

###### 安装包管理
```s
#搜索已经安装的docker
sudo yum list installed | grep docekr
# 输出
#docker.x86_64 2:1.12.6-16.el7.centos @extras 
#docker-client.x86_64 2:1.12.6-16.el7.centos @extras 
#docker-common.x86_64 2:1.12.6-16.el7.centos @extra

# 删除已经安装的docker
sudo yum -y remove docker.x86_64
```

###### sed 命令
```s
# 基本格式
sed [options] 'command' file(s)
```
**sed替换一行**，假设有如下文件：
```s
a
sometext sometext sometext TEXT_TO_BE_REPLACED sometext sometext sometext
b
c
d
```
将含有 `TEXT_TO_BE_REPLACED` 的行替换为 `This line is removed by the admin.`命令如下:
```s
sed -i '/TEXT_TO_BE_REPLACED/c\This line is removed by the admin.' /tmp/foo
```
其中 `-i` 表示直接修改文件，`c` 命令表示将指定行中的所有内容，替换成该选项后面的字符串。该命令的基本格式为：
```s
[address]c\用于替换的新文本
```
**sed 注释某一行** 比如有下面文件 /tmp/foo
```s
This is a 000 line.
This is 000 yet ano000ther line.
This is still yet another line.
```
注释所有含有 `000` 的行。其命令如下：
```s
sed -i '/000/s/^/#/' /tmp/foo
```
其中：
* `/000/` 匹配包含 `000` 的所有行
* `s` 对匹配的行进行替换。
* `^` 表示在行的开始，`#` 表示要插入的字符

**替换匹配正则表达式的行**
```s
sed -E -i s/"^(  version: .*)"/"  version: ${newVersion}"/ ${resourcePath}/"${clusterName}".yaml
```
sed 的分隔符可以替换为其他字符。

参考[sed-功能强大的流式文本编辑器](https://wangchujiang.com/linux-command/c/sed.html)


###### ansible 基本用法
比如有一批机器，需要在这些机器上执行一条命令，hosts 文件如下：
```s
[all]
11.37.51.75
```
需要查看每个节点的版本内核，命令如下：
```s
ansible all -i ./hosts -m command -a "uname -a"
# 有时候 command 模块感觉不是很好用，可以用 shell 模块
ansible all -i ./hosts -m shell -a "docker images | grep e2e"
```

###### nslookup 测试域名解析
```s
# 使用 nslookup 时指定 dns server，假设 dns server 为：10.43.0.10
nslookup xxl-job-admin.xxl-job.svc 10.43.0.10
```

###### ssh 配置免密登录
```s
#一路回车，生成秘钥
ssh-keygen
# 配置免密登录（ -i 参数可以不加）
ssh-copy-id -i .ssh/id_rsa.pub  用户名字@192.168.x.xxx
```

###### 使用 tc 模拟网络丢包和时延

时延，配置 eth0 的时延为 10ms
```s
# 设置时延为 10ms
sudo tc qdisc add dev eth0 root netem delay 10ms

# 查看已有策略
tc qdisc show

# 删除策略
sudo tc qdisc del dev eth0 root netem delay 10ms
```

丢包，配置丢包率为 1%
```s
tc qdisc add dev eth0 root netem loss 1%
```
在上面这个命令中，`root` 其实是指 egress，即出口流量，`netem`是一个 tc 模块，用来模拟网络，参考[https://manpages.ubuntu.com/manpages/kinetic/en/man8/tc-netem.8.html](https://manpages.ubuntu.com/manpages/kinetic/en/man8/tc-netem.8.html)

参考[linux 下使用 tc 模拟网络延迟和丢包](https://blog.csdn.net/weiweicao0429/article/details/17578011)

[https://tldp.org/HOWTO/Traffic-Control-HOWTO/](https://tldp.org/HOWTO/Traffic-Control-HOWTO/) 这个文档有 pdf 版本。


###### vi 复制与删除
vi 注释多行的快捷键为：1）按 Esc 进入“命令模式”；2）使用 Ctrl + v 进入可视区块模式；3）移动 Up / Down 选择要注释的行；4）按 Shift + i 并键入要插入的文件，即 #；5）按 Esc 退出，并等待 1 秒，插入的文本将出现在每一行

![java-javascript](/pics/vi_command.jpg){:height="60%" width="60%"}


参考《[鸟哥私房菜](http://cn.linux.vbird.org/linux_basic/fedora_4/0310vi-fc4.php)》

###### 查看文件被哪个进程占用
```s
$ fuser -v text.txt 
                     USER        PID ACCESS COMMAND
/home/john/text.txt:
                     john      22829 f....  less
```
参考[Find the Process That is Using a File in Linux](https://www.baeldung.com/linux/find-process-file-is-busy)


###### set -euxo pipefail
* -e: 命令出错时，退出整个脚本
* -x: 将执行的命令在控制台打印，多用于调试
* -u: 使用未使用的变量时将出错，也就是变量必须先定义后使用
* -o pipefail: 在使用 pipeline 时，如果一个命令失败了，这个失败的命令的退出码将作为整个 pipeline 的退出码，如使用 `grep some-string /non/existent/file | sort` 命令时，sort 命令不会出错，但是如果 grep 命令出错，整个命令的错误码是 grep 命令的错误码。

参考[set -e, -u, -x, -o pipefail](https://gist.github.com/mohanpedala/1e2ff5661761d3abd0385e8223e16425)

研究下 strace

###### Mac as Proxy
在测试过程中，发现镜像在虚拟机中很难拉下来，但是在mac 上却能拉下来，通过下面命令在 mac 上拉镜像，并推送到虚拟机上，下面脚本中使用了 ansible，得提前配置免密登录。
```s
#!/bin/sh
set -x

docker pull $1
docker save $1 -o "tmp.tar"

ansible vms -i ./hosts -m copy -a "src=./tmp.tar dest=/tmp/tmp.tar"
ansible vms -i ./hosts -m command -a "docker load -i /tmp/tmp.tar"
```
其中当前目录的 `hosts` 配置文件为，有个 vms 分组和三个虚拟机 ip。
```s
[vms]
192.168.31.201
192.168.31.202
192.168.31.203
```
脚本的使用方式如下，第一个参数即为要下载的镜像。
```s
decent@mac% ~/Downloads/image.sh docker.io/istio/examples-bookinfo-details-v1:1.16.2
```

在 mac 中 安装 ansible 方式参考[官方文档](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html):
```s
python3 -m pip install --user ansible
```
如果上面安装的时候下载很慢，可以通过设置`https_proxy`的方式加速，当然你得先有proxy。

###### 参考
[AWK 简明教程](https://coolshell.cn/articles/9070.html)
