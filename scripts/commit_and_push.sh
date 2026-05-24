#!/bin/bash
# scripts/commit_and_push.sh — 自动化提交并创建 PR
# 用法: ./scripts/commit_and_push.sh "commit message" [pr-title]
#
# 自动处理: git身份、gh认证、远程冲突rebase、分支隔离、PR创建

set -euo pipefail

REPO_OWNER="hsliuping"
REPO_NAME="TradingAgents-CN"
FORK_OWNER="xiaodutou"
FORK_REPO="TradingAgents-CN-xdt"
GIT_NAME="xiaodutou"
GIT_EMAIL="xiaodutou@users.noreply.github.com"

# ========== 颜色输出 ==========
RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ========== 前置检查 ==========
check_prerequisites() {
    if ! command -v gh &>/dev/null; then
        error "gh CLI 未安装，请先安装: https://cli.github.com/"
        exit 1
    fi

    if ! gh auth status &>/dev/null 2>&1; then
        error "GitHub 未认证，请运行: gh auth login"
        exit 1
    fi

    if ! git diff --quiet || git diff --cached --quiet 2>/dev/null; then
        : # 有变更，继续
    fi

    # 确保 git 身份已配置
    if [ -z "$(git config user.name)" ] || [ -z "$(git config user.email)" ]; then
        warn "未配置 git 身份，自动设置为 ${GIT_NAME} <${GIT_EMAIL}>"
        git config user.name "${GIT_NAME}"
        git config user.email "${GIT_EMAIL}"
    fi
}

# ========== 参数解析 ==========
COMMIT_MSG="${1:-}"
if [ -z "${COMMIT_MSG}" ]; then
    echo "用法: $0 \"commit message\" [\"PR title\"]"
    echo ""
    echo "示例:"
    echo "  $0 \"fix: 修复 PB 计算错误\""
    echo "  $0 \"fix: 修复 PB 计算\" \"fix: 提升报告数据质量\""
    exit 1
fi

PR_TITLE="${2:-${COMMIT_MSG}}"

# ========== 检查是否有变更 ==========
STAGED_FILES=$(git status --porcelain | grep -c '^[ MARCD]' 2>/dev/null || echo 0)
UNSTAGED_FILES=$(git diff --name-only 2>/dev/null | wc -l)

if [ "${STAGED_FILES}" -eq 0 ] && [ "${UNSTAGED_FILES}" -eq 0 ]; then
    warn "没有需要提交的文件变更"
    exit 0
fi

# ========== 提交变更 ==========
info "提交 ${STAGED_FILES} + ${UNSTAGED_FILES} 个文件..."

# 确保所有修改的文件都被暂存
git add -A

# 执行提交
git -c user.name="${GIT_NAME}" -c user.email="${GIT_EMAIL}" commit -m "${COMMIT_MSG}"

info "提交完成: $(git log --oneline -1)"

# ========== 同步远程变更并推送 ==========
info "同步远程变更并推送..."

# 确保 xdt remote 存在
if ! git remote | grep -q "^xdt$"; then
    git remote add xdt "https://github.com/${FORK_OWNER}/${FORK_REPO}.git"
fi

# 使用 gh 认证推送
git -c credential.helper="!gh auth git-credential" pull xdt main --rebase || {
    warn "Rebase 有冲突，正在中止并尝试合并..."
    git rebase --abort 2>/dev/null || true
    git -c credential.helper="!gh auth git-credential" pull xdt main --no-rebase || {
        error "拉取远程变更失败，请手动处理冲突后重试"
        exit 1
    }
}

# 推送当前分支（如果在 feature 分支则推该分支，否则推 main）
CURRENT_BRANCH=$(git branch --show-current)
if [ "${CURRENT_BRANCH}" = "main" ]; then
    git -c credential.helper="!gh auth git-credential" push xdt main
else
    git -c credential.helper="!gh auth git-credential" push xdt "${CURRENT_BRANCH}" 2>/dev/null || {
        git -c credential.helper="!gh auth git-credential" push --set-upstream xdt "${CURRENT_BRANCH}"
    }
fi

info "推送完成"

# ========== 创建 PR ==========
info "创建 Pull Request..."

if [ "${CURRENT_BRANCH}" = "main" ]; then
    # main 分支：使用 xiaodutou:main -> hsliuping:main
    PR_URL=$(gh pr create \
        --repo "${REPO_OWNER}/${REPO_NAME}" \
        --base main \
        --head "${FORK_OWNER}:main" \
        --title "${PR_TITLE}" \
        --body "## Summary

$(git log --format='%b' -1 | head -30)

Generated with Claude Code" 2>&1) || true
else
    PR_URL=$(gh pr create \
        --repo "${REPO_OWNER}/${REPO_NAME}" \
        --base main \
        --head "${FORK_OWNER}:${CURRENT_BRANCH}" \
        --title "${PR_TITLE}" \
        --body "Generated with Claude Code" 2>&1) || true
fi

if echo "${PR_URL}" | grep -q "^https://"; then
    info "PR 创建成功: ${PR_URL}"
else
    warn "PR 可能已存在或创建失败: ${PR_URL}"
fi
