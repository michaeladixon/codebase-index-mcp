//! Sample library crate.

pub const MAX_DEPTH: usize = 16;

/// Kinds of nodes in the tree.
pub enum NodeKind {
    Leaf,
    Branch,
}

/// A tree node.
pub struct Node {
    pub kind: NodeKind,
    pub children: Vec<Node>,
}

/// Anything that can be measured.
pub trait Measure {
    fn depth(&self) -> usize;
}

impl Node {
    /// Build a leaf node.
    pub fn leaf() -> Self {
        Node { kind: NodeKind::Leaf, children: Vec::new() }
    }

    pub fn push(&mut self, child: Node) {
        self.children.push(child);
    }
}

impl Measure for Node {
    fn depth(&self) -> usize {
        1 + self.children.iter().map(|c| c.depth()).max().unwrap_or(0)
    }
}
