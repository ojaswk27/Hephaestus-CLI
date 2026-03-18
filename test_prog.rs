// test_prog.rs — intentionally slow bubble sort in Rust
fn bubble_sort(arr: &mut [i32]) {
    let n = arr.len();
    for i in 0..n - 1 {
        for j in 0..n - i - 1 {
            if arr[j] > arr[j + 1] {
                arr.swap(j, j + 1);
            }
        }
    }
}

fn main() {
    const N: usize = 8000;
    let mut arr: Vec<i32> = (1..=N as i32).rev().collect();
    for _ in 0..5 {
        bubble_sort(&mut arr);
    }
    println!("sorted[0]={} sorted[{}]={}", arr[0], N - 1, arr[N - 1]);
}
