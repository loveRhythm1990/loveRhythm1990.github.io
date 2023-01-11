---
layout:     post
title:      "leetcode题解"
subtitle:   " \"持续更新...\""
date:       2020-2-22 22:06:00
author:     "weak old dog"
header-img-credit: false
tags:
    - 算法
---

### [33. 搜索旋转排序数组](https://leetcode-cn.com/problems/search-in-rotated-sorted-array/)

假设按照升序排序的数组在预先未知的某个点上进行了旋转。

( 例如，数组 [0,1,2,4,5,6,7] 可能变为 [4,5,6,7,0,1,2] )。

搜索一个给定的目标值，如果数组中存在这个目标值，则返回它的索引，否则返回 -1 。

你可以假设数组中不存在重复的元素。
你的算法时间复杂度必须是 O(log n) 级别。

示例 1:
输入: nums = [4,5,6,7,0,1,2], target = 0   输出: 4

示例 2:
输入: nums = [4,5,6,7,0,1,2], target = 3   输出: -1

这个题关键在于找确定关系，本题的确定关系为:`给定任何一个数组的下标，下标的左右两侧中，一定有一侧是有序的`，可以自己画个图看看。有了这个确定关系，我们在决定向左找还是向右找的时候，只要在有序的那边看一下target是否在那边，这个很好确定，因为其是有序的，我们知道最大值最小值，比较一下就可以了。

那有个问题是如何判断那边是有序的，其实只要跟两边的值比较一下就可以了，假设左边是l，右边是h，中间是m，那么如果：nums[m] > nums[l]，那么左边是有序的，如果nums[m] < nums[l]，那么右边是有序的。同样的，跟右边的值比较也是可以的：如果nums[m] < nums[h]那么右边是有序的，如果nums[m] > nums[h]，那么左边是有序的。但是对于二分查找有个问题，那就是m是有可能等于l的，即下标相同，这种情况下，对于`[2, 1]target=1`这种case，比较难处理（不容易理解）。所以跟右边比较好。贴出两种方式的代码。

跟右边比较：
```go
func search(nums []int, target int) int {
	l, h := 0, len(nums)-1

	for l <= h {
		m := (l+h)/2
		if nums[m] == target {
			return m
		}

		if nums[h] < nums[m] { // left is ordered
			if nums[m] > target && target >= nums[l] {
				h = m - 1
			} else {
				l = m + 1
			}
		} else { // right is ordered
			if nums[m] < target && nums[h] >= target {
				l = m + 1
			} else {
				h = m - 1
			}
		}
	}
	return -1
}
```
跟左边比较：
```go
func search2(nums []int, target int) int {
	l, h := 0, len(nums)-1

	for l <= h {
		m := (l+h)/2
		if nums[m] == target {
			return m
		}

		if nums[l] <= nums[m] { // left is ordered
			if nums[m] > target && target >= nums[l] {
				h = m - 1
			} else {
				l = m + 1
			}
		} else { // right is ordered
			if nums[m] < target && nums[h] >= target {
				l = m + 1
			} else {
				h = m - 1
			}
		}
	}
	return -1
}
```


### [211. 添加与搜索单词 - 数据结构设计](https://leetcode-cn.com/problems/add-and-search-word-data-structure-design/)
设计一个支持以下两种操作的数据结构：
```s
void addWord(word)
bool search(word)
```
search(word) 可以搜索文字或正则表达式字符串，字符串只包含字母 . 或 a-z 。 . 可以表示任何一个字母。

示例:
```s
addWord("bad")
addWord("dad")
addWord("mad")
search("pad") -> false
search("bad") -> true
search(".ad") -> true
search("b..") -> true
```
说明:

你可以假设所有单词都是由小写字母 a-z 组成的。

这个题考虑到的解法是用trie树，即字典树，在trie树中，每个节点表示一个word中的一个字符，在word结尾的那个节点存放该word，表示有这么一个word存在，在trie树中，有多少个不同的字符一个节点就有多少个孩子，查找时，从根节点向孩子节点依次查找每个字符。添加word时也是依次添加的，如果遇到孩子节点为nil，是需要create一个的。

另外在go语言中，结构体是不能嵌套结构体自身的，否则在初始化的时候会无限递归，嵌套指针是可以的。

代码如下，可能不是最优的，只记录思路，日后可以优化一下，
```go
// implements trie tree by golang
type WordDictionary struct {
	root *trieNode
}

type trieNode struct {
	children [27]*trieNode
	// contains a string ?
	str string
}

/** Initialize your data structure here. */
func Constructor() WordDictionary {
	return WordDictionary{&trieNode{}}
}

/** Adds a word into the data structure. */
func (this *WordDictionary) AddWord(word string) {
	this.addToChild(word, this.root)
}

// add character into root's children
func (this *WordDictionary) addToChild(word string, root *trieNode) {
	bs := []byte(word)

	parent := root
	for _, c := range bs {
		// get index
		index := 0
		if c == '.' {
			index = 26
		} else {
			index = int(c - 'a')
		}

		if parent.children[index] == nil {
			// create this child
			child := &trieNode{}
			parent.children[index] = child

			// dfs
			parent = child
		} else {
			parent = parent.children[index]
		}
	}

	parent.str = word
}

/** Returns if the word is in the data structure. A word could contain the dot character '.' to represent any one letter. */
func (this *WordDictionary) Search(word string) bool {
	if this.searchFromChildren(word, this.root) {
		return true
	}
	return false
}

func (this *WordDictionary) searchFromChildren(word string, node *trieNode) bool {
	bs := []byte(word)

	parent := node
	for i := 0; i < len(bs); i++ {

		// bs[i] is the character should to search in child (children) node
		if bs[i] == '.' {
			// find rest word in all children
			for _, n := range parent.children {
				if n == nil {
					continue
				}
				// check the last character
				if i == len(bs) - 1 {
					if len(n.str) != 0 {
						return true
					}
				}

				if this.searchFromChildren(word[i+1:], n) {
					return true
				}
			}
			return false
		} else {
			// child index should search
			index := bs[i] - 'a'
			if parent.children[index] == nil {
				// child is nil, search failure
				return false
			}

			// check whether last node contains word
			if i == len(bs) - 1 {
				if len(parent.children[index].str) == 0 {
					return false
				}
				return true
			}

			return this.searchFromChildren(word[i+1:], parent.children[index])
		}
	}
	return false
}
```
### [实现Trie树](https://leetcode-cn.com/problems/implement-trie-prefix-tree/)
实现一个 Trie (前缀树)，包含 insert, search, 和 startsWith 这三个操作。

**示例**:
```go
Trie trie = new Trie();

trie.insert("apple");
trie.search("apple");   // 返回 true
trie.search("app");     // 返回 false
trie.startsWith("app"); // 返回 true
trie.insert("app");   
trie.search("app");     // 返回 true
```
**说明**:

你可以假设所有的输入都是由小写字母 a-z 构成的。
保证所有输入均为非空字符串。

事实证明，只要60行代码就能实现Trie树，之前都是想复杂了
```go
type Trie struct {
    root *Node
}

type Node struct {
    children [26]*Node
    word string
}

/** Initialize your data structure here. */
func Constructor() Trie {
    return Trie{
        &Node{},
    }
}

/** Inserts a word into the trie. */
func (this *Trie) Insert(word string)  {
    
    bs := []byte(word)
    parent := this.root
    
    for i := 0; i < len(bs); i ++ {
        index := bs[i] - 'a'
        if parent.children[index] == nil {
            parent.children[index] = &Node{}
        }
        parent = parent.children[index]
    }
    parent.word = word
}


/** Returns if the word is in the trie. */
func (this *Trie) Search(word string) bool {
    bs := []byte(word)
    parent := this.root
    
    for i := 0; i < len(bs); i++ {
        index := bs[i] - 'a'
        if parent.children[index] == nil {
            return false
        }
        parent = parent.children[index]
    }
    if parent.word == "" {
        return false
    }
    return true
}


/** Returns if there is any word in the trie that starts with the given prefix. */
func (this *Trie) StartsWith(prefix string) bool {
    bs := []byte(prefix)
    parent := this.root
    
    for i := 0; i < len(bs); i++ {
        index := bs[i] - 'a'
        if parent.children[index] == nil {
            return false
        }
        parent = parent.children[index]
    }
    return true
}
```