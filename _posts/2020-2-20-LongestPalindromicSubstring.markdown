---
layout:     post
title:      "最长回文子串"
date:       2020-2-20 15:55:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 数据结构/算法
---

leetcode题目：
[Given a string s, find the longest palindromic substring in s. You may assume that the maximum length of s is 1000.](https://leetcode-cn.com/problems/longest-palindromic-substring)

Example 1:

Input: "babad"
Output: "bab"
Note: "aba" is also a valid answer.

Example 2:

Input: "cbbd"
Output: "bb"

这里用动态规划的方法，`Manacher 算法`暂时不考虑。

**思路**：
引入bool类型辅助数组`dp[i][j]`，元素`dp[i][j]`表示从字符串的i下标到j下标是否为回文，如果是则为true，否则为false，并且这这里，我们限定`i<=j`（`i<=j`意味着，我们只使用了二维矩阵的上半矩阵，并且使用了对角线），因此：
```go
// 任何一个单一字符构成回文
dp[i][i] = true

// 相邻的两个字符相同，也构成回文
if s[i] == s[i+1] {
	dp[i][i+1] = true 
}

// 第一个字符和第三个字符相同，不管中间那个是啥字符，这三个字符都构成回文
if s[i] == s[i+2] {
	dp[i][i+2] = true
}

// 如果中间的字符能构成回文，并且两端的字符相等，也构成回文
if s[i] == s[k] && dp[i+1][k-1] {
	dp[i][k] = true
}
```
动态规划的思想是利用已经计算出来的结果，推倒现在的结果，那么意味着`dp[i+1][k-1]`要比`dp[i][k]`优先计算。如下图所示，`dp[i+1][k-1]`是`dp[i][k]`的左下角，我们只要按照列顺序，从下到上遍历就可以了，列顺序意味着先处理左边，再处理右边。

![java-javascript](/img/in-post/longestPS/Picture1.png)

完整代码如下：
```golang
func longestPalindrome(s string) string {

	length := len(s)
	// start index and length of palindromic substring
	left, len := 0, 1

	dp := make([][]bool, length)
	for i := range dp {
		dp[i] = make([]bool, length)
	}

	for j := 0; j < length; j++ {
		dp[j][j] = true

		for i := 0; i < j; i++ {
			// following if states includes all cases of forming palindromic
			if s[j] == s[i] && (j-i < 2 || dp[i+1][j-1]) {
				dp[i][j] = true
			}
			// update max value
			if dp[i][j] && len < j-i+1 {
				len = j - i + 1
				left = i
			}
		}
	}
	return s[left : left+len]
}
```
