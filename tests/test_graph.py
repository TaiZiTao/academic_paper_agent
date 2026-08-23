"""
LangGraph Workflow 测试

覆盖：
- GraphState 创建
- build_graph() 编译成功
- 各 Node 独立行为
- 完整 Workflow 执行
- State 流转正确性

使用骨架 Node，不调用真实 LLM / Retriever / OpenAI。
"""

import pytest

from app.graph import (
    GraphState,
    build_graph,
    generation_node,
    intent_router_node,
    query_rewrite_node,
    retrieval_node,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def base_state():
    """最小有效 GraphState"""
    return GraphState(
        session_id="test_session_001",
        query="什么是GraphRAG？",
        messages=[],
        retrieved_documents=[],
        answer="",
        citations=[],
        error="",
    )


@pytest.fixture
def compiled_graph():
    """编译后的 Workflow"""
    return build_graph()


# ============================================================
# GraphState Tests
# ============================================================

class TestGraphState:
    """GraphState 数据结构测试"""

    def test_create_minimal_state(self):
        """最小 State 可以正常创建"""
        state: GraphState = {"session_id": "s1", "query": "hello"}
        assert state["session_id"] == "s1"
        assert state["query"] == "hello"

    def test_create_full_state(self, base_state):
        """完整 State 包含所有预期字段"""
        assert base_state["session_id"] == "test_session_001"
        assert base_state["query"] == "什么是GraphRAG？"
        assert base_state["messages"] == []
        assert base_state["retrieved_documents"] == []
        assert base_state["answer"] == ""
        assert base_state.get("error", "") == ""

    def test_state_defaults(self):
        """TypedDict(total=False) 允许部分字段创建"""
        state: GraphState = {"query": "minimal"}
        assert state["query"] == "minimal"
        assert state.get("session_id", "") == ""


# ============================================================
# build_graph Tests
# ============================================================

class TestBuildGraph:
    """Workflow 编译测试"""

    def test_build_graph_returns_compiled(self, compiled_graph):
        """build_graph 返回已编译的 Graph"""
        assert compiled_graph is not None

    def test_graph_has_nodes(self, compiled_graph):
        """Graph 包含预期的 4 个 Node"""
        nodes = compiled_graph.get_graph().nodes
        node_names = {n for n in nodes if n not in ("__start__", "__end__")}
        assert "query_rewrite" in node_names
        assert "intent_router" in node_names
        assert "retrieval" in node_names
        assert "generation" in node_names


# ============================================================
# Node Tests (独立行为)
# ============================================================

class TestQueryRewriteNode:
    """查询改写 Node 测试"""

    def test_passthrough_query(self):
        """Phase 7: 查询直接透传"""
        state: GraphState = {"query": "什么是GraphRAG？"}
        result = query_rewrite_node(state)
        assert result["rewritten_query"] == "什么是GraphRAG？"

    def test_empty_query(self):
        """空查询不崩溃"""
        state: GraphState = {"query": ""}
        result = query_rewrite_node(state)
        assert result["rewritten_query"] == ""


class TestIntentRouterNode:
    """意图路由 Node 测试"""

    def test_returns_knowledge_qa(self):
        """Phase 7: 所有查询路由到 knowledge_qa"""
        state: GraphState = {"rewritten_query": "any query"}
        result = intent_router_node(state)
        assert result["intent"] == "knowledge_qa"


class TestRetrievalNode:
    """检索 Node 测试（Phase 10: async node）"""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        """无 config → 返回空文档列表（骨架降级）"""
        state: GraphState = {"rewritten_query": "test"}
        result = await retrieval_node(state)
        assert result["retrieved_documents"] == []


class TestGenerationNode:
    """答案生成 Node 测试（Phase 10: async node）"""

    @pytest.mark.asyncio
    async def test_returns_placeholder_answer(self):
        """无 config → 返回骨架模板回答"""
        state: GraphState = {"query": "什么是GraphRAG？"}
        result = await generation_node(state)
        assert "GraphRAG" in result["answer"]
        assert result["citations"] == []

    @pytest.mark.asyncio
    async def test_answer_not_empty(self):
        """回答不为空字符串"""
        state: GraphState = {"query": "test"}
        result = await generation_node(state)
        assert len(result["answer"]) > 0


# ============================================================
# Workflow Execution Tests (Phase 10: ainvoke)
# ============================================================

class TestWorkflowExecution:
    """完整 Workflow 执行测试"""

    @pytest.mark.asyncio
    async def test_invoke_returns_state(self, compiled_graph, base_state):
        """ainvoke 返回包含所有预期字段的 State"""
        result = await compiled_graph.ainvoke(base_state)

        assert result["session_id"] == "test_session_001"
        assert "rewritten_query" in result
        assert "intent" in result
        assert "retrieved_documents" in result
        assert "answer" in result

    @pytest.mark.asyncio
    async def test_query_flows_through_pipeline(self, compiled_graph):
        """query 从输入到 answer 的完整流转"""
        state: GraphState = {"query": "如何部署企业知识库？"}
        result = await compiled_graph.ainvoke(state)

        assert result["rewritten_query"] == "如何部署企业知识库？"
        assert result["intent"] == "knowledge_qa"
        assert result["retrieved_documents"] == []
        assert "如何部署企业知识库" in result["answer"]

    @pytest.mark.asyncio
    async def test_invoke_preserves_input(self, compiled_graph, base_state):
        """ainvoke 不修改原始输入 State"""
        original_query = base_state["query"]
        await compiled_graph.ainvoke(base_state)
        assert base_state["query"] == original_query

    @pytest.mark.asyncio
    async def test_multiple_invocations(self, compiled_graph):
        """多次 ainvoke 之间相互独立"""
        r1 = await compiled_graph.ainvoke({"query": "问题A"})
        r2 = await compiled_graph.ainvoke({"query": "问题B"})

        assert r1["answer"] != r2["answer"]
        assert "问题A" in r1["answer"]
        assert "问题B" in r2["answer"]

    @pytest.mark.asyncio
    async def test_state_no_error_in_normal_flow(self, compiled_graph, base_state):
        """正常流程不产生 error"""
        result = await compiled_graph.ainvoke(base_state)
        assert result.get("error", "") == ""

    @pytest.mark.asyncio
    async def test_node_order_is_correct(self, compiled_graph, base_state):
        """验证 Node 执行顺序"""
        result = await compiled_graph.ainvoke(base_state)

        assert result["rewritten_query"] == result["query"]
        assert result["intent"] == "knowledge_qa"
        assert result["retrieved_documents"] == []
        assert len(result["answer"]) > 0
