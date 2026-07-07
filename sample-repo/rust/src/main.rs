use sample::{Measure, Node};

fn build_tree() -> Node {
    let mut root = Node::leaf();
    root.push(Node::leaf());
    root
}

fn main() {
    let tree = build_tree();
    println!("depth {}", tree.depth());
}
