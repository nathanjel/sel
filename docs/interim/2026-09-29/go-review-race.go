package main
import (
 "sync"
 "os"
 "fmt"
 "github.com/nathanjel/sel/go/sel"
)
func main() {
 var wg sync.WaitGroup
 if len(os.Args)>1 && os.Args[1]=="slot" {
  p:=sel.MustCompile(`R["x"]`)
  for i:=0;i<8;i++ { wg.Add(1); go func(i int) { defer wg.Done(); for j:=0;j<100;j++ {
   r:=sel.NewRecordFromEntries([]sel.Entry{{Key:fmt.Sprint(i),Val:sel.NewInt(0)},{Key:"x",Val:sel.NewInt(1)}})
   _,err:=p.Run(sel.NewNone().Set("R",r)); if err!=nil {panic(err)}
  }}(i) }
 } else {
  for i:=0;i<8;i++ { wg.Add(1); go func(i int) { defer wg.Done(); for j:=0;j<30;j++ {
   _,err:=sel.Eval(fmt.Sprintf("ROUND(1, %d)",20+i+j),nil); if err!=nil {panic(err)}
  }}(i) }
 }
 wg.Wait()
}
