import sys
from .korail_client import ReserveHandler

sys.setrecursionlimit(10**7)


# def reserve(self, depDate, srcLocate, dstLocate, depTime='000000', trainType=TrainType.KTX, special=ReserveOption.GENERAL_FIRST, chatId="", maxDepTime'2400'):
class BackProcess(object):

    def __init__(self):
        self.username = sys.argv[1]
        self.password = sys.argv[2]
        self.depDate = sys.argv[3]
        self.srcLocate = sys.argv[4]
        self.dstLocate = sys.argv[5]
        self.depTime = sys.argv[6]
        self.trainType = sys.argv[7]
        self.specialInfo = sys.argv[8]
        self.chatId = sys.argv[9]
        self.maxDepTime = sys.argv[10]
        self.reserve_handler = ReserveHandler()
        self.reserve_handler.login(self.username, self.password)

    def run(self):

        try:
            self.reserve_handler.reserve(
                self.depDate,
                self.srcLocate,
                self.dstLocate,
                self.depTime,
                self.trainType,
                self.specialInfo,
                self.chatId,
                self.maxDepTime,
            )
        except Exception as e:
            print(e)
            msg = "에러 발생 : {}".format(e)
            self.reserve_handler.sendBotStateChange(self.chatId, msg, 0)
        print("Reserve Job for {} is end".format(self.username))


proc1 = BackProcess()
proc1.run()
