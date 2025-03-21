---
layout:     post
title:      "vscode 快捷键及使用技巧"
date:       2023-3-11 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - 运维
---

**目录**

- [生成 markdown 目录](#生成-markdown-目录)
- [插入图片](#插入图片)
- [mac 部分键盘符号](#mac-部分键盘符号)
- [mac vscode 快捷键](#mac-vscode-快捷键)
- [goland 试用](#goland-试用)
- [为 git 配置代理](#为-git-配置代理)

记录一些提升能效的快捷键、技巧等。

### 生成 markdown 目录
在 vscode 中安装 `Markdown All in One` 插件。文章写完后，按下快捷键 `command + shift + p`，输入`Markdown All in One: Create Table of Contents` 会自动生成文章的目录。

如果文章目录有更新，目录会自动更新。

### 插入图片
使用下面命令插入图片，所有的图片我都放在了根目录下的 `/pics` 文件夹下。

```sh
![java-javascript](/pics/somepic.png){:height="60%" width="60%"}
```

### mac 部分键盘符号
![java-javascript](/pics/mac-jianpan.png){:height="30%" width="30%"}

### mac vscode 快捷键
针对 golang 环境，其他环境可能有出入。

设置快捷键的方式是: 1. 使用 `cmd+shift+p`打开命令面板。2.选择 `Preferences: Open Keyboard Shortcuts (JSON)`

全局控制快捷键（快捷键中的字母都是小写）:

| 功能      | 快捷键 |
| ----------- | ----------- |
| 打开命令面板      | cmd + shift + p       |
| 打开（关闭）终端   | cmd + 反引号        |
| 搜索文件名 | cmd + p |
|全局搜索`关键字` |cmd + shift + f|
|打开`插件`扩展|cmd + shift + x|

侧边栏快捷键，打开文件的快捷键有可能被`重命名`快捷键覆盖，需要在快捷键系统中重新配置。

| 功能      | 快捷键 |
| ----------- | ----------- |
|显示与隐藏`侧边栏`|cmd + b|
|聚焦到`侧边栏`|cmd + shift + e|
|展开侧边栏的文件夹|`->` 或者 enter |
|折叠侧边栏的文件夹|`<-`|
|打开文件| enter|


编辑区快捷键(其中上一次光标的位置和下一次光标的位置进行了自定义):

| 功能      | 快捷键 |
| ----------- | ----------- |
| 运行代码   | ctrl + option + n  |
|在`当前文件`搜索(替换)关键字 | cmd + f |
|删除一行|cmd + x|
|粘贴一行|cmd + v|
|查找变量的`定义`与`引用`|F12|
|查找 interface 的实现|cmd + F12|
|~~回到上一次光标位置~~| ctrl + - (减号)|
|~~前进到下一个光标的位置~~| ctrl + shift + -|
|回到上一次光标位置| alt + `<-` |
|前进到下一个光标的位置| alt + `->` |

gitlens 快捷键，这个是自定义快捷键（Toggel line blame）：

| 功能      | 快捷键 |
| ----------- | ----------- |
| 查看(关闭)行注释   | cmd + shift + b  |

### goland 试用
从 jetbra 官方网站下载最新版，从 Russia 老哥那里下载一个 jetbra.zip 压缩包，解压之后，script 目录下面有一个 `install.sh` 脚本，执行一下。

然后到其网站复制一下体验码，复制到 Goland 中，然后就可以体验使用 Goland 了。其网站为：aHR0cHM6Ly8zLmpldGJyYS5pbi8=

或者：aHR0cHM6Ly9qZXRicmEuaW4vcw==

或者直接从拼多多花几块钱上买一个。

### 为 git 配置代理

首先配置 http 代理
```sh
git config --global --add http.proxy http://USERNAME:PASSWORD@PROXY_ADDRESS:PROXY_PORT
git config --global --add https.proxy http://USERNAME:PASSWORD@PROXY_ADDRESS:PROXY_PORT
```

然后将所有 git 协议换成 http 协议
```sh
git config --global url."https://".insteadOf git://
git config --global url."https://github.com/".insteadOf git@github.com: 
```
(don't remove the final colon)