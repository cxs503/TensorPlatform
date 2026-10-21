"""Trusted unittest collector mounted read-only; captures methods and subtests separately."""
import contextlib
import io
import json
import sys
import traceback
import unittest

class BoundedLog(io.StringIO):
    def write(self, value):
        room=max(0,12000-self.tell())
        super().write(value[:room])
        return len(value)

class Results(unittest.TestResult):
    def __init__(self):
        super().__init__();self.methods=[];self.subtests=[];self.active={};self.truncated=False
    def startTest(self,test):
        super().startTest(test)
        row={'id':test.id()[:500],'status':'passed'}
        self.active[id(test)]=row
        if len(self.methods)<512:self.methods.append(row)
        else:self.truncated=True
    def mark(self,test,status):
        if id(test) not in self.active:
            self.truncated=True
            return
        self.active[id(test)]['status']=status
    def addFailure(self,test,err):super().addFailure(test,err);self.mark(test,'failure')
    def addError(self,test,err):super().addError(test,err);self.mark(test,'error')
    def addSkip(self,test,reason):super().addSkip(test,reason);self.mark(test,'skipped')
    def addExpectedFailure(self,test,err):super().addExpectedFailure(test,err);self.mark(test,'expected_failure')
    def addUnexpectedSuccess(self,test):super().addUnexpectedSuccess(test);self.mark(test,'unexpected_success')
    def addSubTest(self,test,subtest,err):
        super().addSubTest(test,subtest,err)
        status='passed' if err is None else ('failure' if issubclass(err[0],test.failureException) else 'error')
        if err:self.mark(test,status)
        if len(self.subtests)<512:self.subtests.append({'id':str(subtest)[:500],'parent_id':test.id()[:500],'status':status})
        else:self.truncated=True

def main():
    sys.path.insert(0,'/work');log=BoundedLog();result=Results()
    try:
        with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            suite=unittest.defaultTestLoader.discover('/work',pattern='test_*.py')
            suite.run(result)
        counts={s:sum(r['status']==s for r in result.methods) for s in ['passed','failure','error','skipped','expected_failure','unexpected_success']}
        counts.update({'subtests_'+s:sum(r['status']==s for r in result.subtests) for s in ['passed','failure','error']})
        errors='\n'.join(str(test)+':\n'+detail for test,detail in result.errors+result.failures)
        payload={'schema_version':1,'status':'completed','tests_run':result.testsRun,'methods':result.methods,'subtests':result.subtests,'counts':counts,'truncated':result.truncated,'log':(log.getvalue()+'\n'+errors)[:24000]}
        # Zero discovered methods or skipped suites cannot qualify a delivery.
        passed=result.wasSuccessful() and counts['passed']>0 and not result.truncated
        print(json.dumps(payload,ensure_ascii=False));return 0 if passed else 1
    except BaseException as e:
        print(json.dumps({'schema_version':1,'status':'runner-error','error_class':type(e).__name__,'log':traceback.format_exc()[-8000:]}));return 2

if __name__=='__main__':sys.exit(main())
