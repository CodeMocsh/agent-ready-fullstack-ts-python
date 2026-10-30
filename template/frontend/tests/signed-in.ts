export function signedIn<T>(_screen: string, draw: () => Promise<T>): Promise<T> {
  return draw();
}
