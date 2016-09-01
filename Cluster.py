import os
try:
	import pp
except ImportError:
	if not os.getenv('SILENT'):
		print 'pp modude not installed'
	pass
import types
import threading
import time
import copy

from Utilities import iterRange, Utilities
from ValueParser import ValueParser
from ObjectBase import defaultdict, runFromTerminal
from FileHandler import FileNameGetter

class T:
	def run(self, cmd):
		os.system(cmd)


''' Function taken from parallel python '''
def _detect_ncpus():
	if os.environ.get('NB_CPUS'):
		return int(os.environ['NB_CPUS'])
	"""Detects the number of effective CPUs in the system"""
	#for Linux, Unix and MacOS
	if hasattr(os, "sysconf"):
		if "SC_NPROCESSORS_ONLN" in os.sysconf_names:
		#Linux and Unix
			ncpus = os.sysconf("SC_NPROCESSORS_ONLN")
			if isinstance(ncpus, int) and ncpus > 0:
				return ncpus
		else:
		#MacOS X
			return int(os.popen2("sysctl -n hw.ncpu")[1].read())
	#for Windows
	if "NUMBER_OF_PROCESSORS" in os.environ:
		ncpus = int(os.environ["NUMBER_OF_PROCESSORS"])
		if ncpus > 0:
			return ncpus
	#return the default value
	return 1

def _getNbAvailableCpus():
	fh = os.popen('uptime')
	resStr = fh.read()
	fh.close()
	if 'average' not in resStr:
		raise NotImplementedError('uptime result does not contain "average" keyword: "%s"' % resStr)
	try:
		busyCpu = int(float(resStr.split('average')[-1].split(':')[-1].strip().split(' ')[0].rstrip(',').replace(',', '.')))
	except:
		print 'Unexpected uptime format: "%s"' % resStr
		raise
	return max(_detect_ncpus() - busyCpu, 1)


class StoppableThread (threading.Thread):
	"""Thread class with a stop() method. The thread itself has to check
	regularly for the stopped() condition."""

	def __init__(self):
		self._stop = threading.Event()
		super(StoppableThread, self).__init__()

	def stop(self):
		self._stop.set()

	def stopped(self):
		return self._stop.isSet()


class ThreadFunc(threading.Thread):
	def __init__(self, func, *args):
		self.__func = func
		self._args = args
		self._error = None
		threading.Thread.__init__(self)

	def run(self):
		try:
			self._res = self.__func(*self._args)
		except:
			import Utilities
			self._error = Utilities.getLastErrorMessage()
			raise


class ThreadManager:
	RUNNING = '|<*RUNNING*>|'
	TASK_ID_VAR = None
	
	def __init__(self, maxThreadNb = None, keepResults = False, timeout = None, adjustProcNb = False):
		self._resList = []
		self.__threadList = []
		self.__maxThreadNb = maxThreadNb
		self.__keepResults = keepResults
		self.__timeout = timeout
		self.__nbProc = 1
		self.__adjustProcNb = adjustProcNb
		self.__canProcBeAdjusted = False
		self.__maxProcNb = _detect_ncpus()
		
	def __del__(self):
		print 'Waiting for all jobs to complete before exiting'
		self.wait()
		
	def getRunningJobNb(self):
		self.__removeFinishedJobs()
		return len(self.__threadList)
	
	getNbRunningJobs = getRunningJobNb
	
	def clear(self):
		self._resList = []

	def __iter__(self):
		if not self.__keepResults:
			raise NotImplementedError
		return iter(self._resList)

	def __removeFinishedJobs(self):
		newThreadList = []
		while len(self.__threadList):
			thread = self.__threadList.pop(0)
			if thread._error:
				import Log
				Log.error('ERROR in thread:' + thread._error)
				raise NotImplementedError
			if thread.isAlive():
				newThreadList.append(thread)
			else:
				self.__canProcBeAdjusted = True
				if self.__keepResults:
					#print 'KEEEP', thread._res
					self._resList.append((thread._args, thread._res))
		self.__threadList = newThreadList

	def wait(self, callbackFunc = None, *args):
		while len(self.__threadList):
			thread = self.__threadList.pop(0)
			thread.join(self.__timeout)
			if not thread._error and self.__keepResults:
				if thread.isAlive():
					#thread.stop()
					#thread.join()
					self._resList.append((thread._args, self.RUNNING))
					continue
				else:
					self._resList.append((thread._args, thread._res))
			if callbackFunc is not None:
				print 'call', thread._res
				callbackFunc(thread._res, *args)
		
	waitUntilClusterIsFree = wait

	def __getNbUsedThreads(self):
		return sum([thread._nbProc for thread in self.__threadList])
	
	def __canNewThreadBeCreated(self):
		self.__removeFinishedJobs()
		if self.__canProcBeAdjusted and self.__adjustProcNb:
			oldMax = self.__maxThreadNb
			self.__maxThreadNb = max(self.__maxThreadNb, _getNbAvailableCpus() + len(self.__threadList))
			if oldMax != self.__maxThreadNb:
				print 'Extending max thread nb from %d to %d' % (oldMax, self.__maxThreadNb)
		return self.__maxThreadNb is None or self.__getNbUsedThreads() <= self.__maxThreadNb - self.__nbProc

	def __waitForThreadToFinish(self):
		while True:
			if self.__canNewThreadBeCreated():
				return
			time.sleep(1)

	def submit(self, func, *args):
		#print 'Submit', func, args
		#print self.__maxThreadNb, _getNbAvailableCpus(), len(self.__threadList), self.__nbProc
		self.__waitForThreadToFinish()
		thread = ThreadFunc(func, *args)
		thread.daemon = True
		thread._nbProc = self.__nbProc
		self.__removeFinishedJobs()
		self.__threadList.append(thread)
		#print 'Running thread'
		thread.start()	
		
	def submitJobAndGetId(self, command, memory = None, nbProc = 1, dependentJobId = None, jobName = None, machineToUseList = None, errorFile = None, outputFile = None, queue = None, machineToExcludeList = None, email = None, walltime = 604800, node = 1, scriptName = None, expectedOutputFile = None, optionList = None):
		appendTouch = False
		if ' touch ' in command:
			cmdList = command.split(' && ')
			if 'touch' in cmdList[-1].split():
				command = ' && '.join(cmdList[:-1])
				cmdList = [command, cmdList[-1]]
				appendTouch = True
		if outputFile and '>' not in command:
			command += ' 1> %s' % outputFile
		if errorFile:
			command += ' 2> %s' % errorFile
		if appendTouch:
			cmdList[0] = command
			command = ' && '.join(cmdList)
		self.__nbProc = nbProc
		self.submit(Utilities.mySystem, command)

	
class ClusterBase:
	_nodeTuple = ()

	def __init__(self, nbCpus = None, nodeTuple = None, port = None, sleepTime = 1):
		self._nbCpus = nbCpus
		if nodeTuple is not None:
			self._nodeTuple = nodeTuple
		if port:
			self._nodeTuple = tuple([node + ':%d' % port for node in self._nodeTuple])
		# case where we do not want to parallelize i.e. linear case
		if nbCpus == 1:
			self._jobServer = JobServer()
		elif nbCpus != -1:
			if nbCpus is not None:
				# Creates jobserver with ncpus workers
				self._jobServer = pp.Server(nbCpus, ppservers = self._nodeTuple, secret = '')
			else:
				self._jobServer = pp.Server(ppservers = self._nodeTuple, secret = '')
			#print "Starting pp with", self._jobServer.get_ncpus(), "workers", self._nodeTuple
		self.__sleepTime = sleepTime
		self.__jobList = []

	def getNbSubmittedJobs(self):
		return len(self.__jobList)
	
	def getNbActiveJobs(self):
		return len([job for job in self.__jobList if type(job) != types.StringType and not job.finished])
	
	getNbRunningJobs = getNbActiveJobs
	
	def __waitForJobs(self):
		while self.getNbActiveJobs() > 10 * self._jobServer.get_ncpus():
			#print 'There'
			time.sleep(self.__sleepTime)
			#newJobList = []
			#self.__jobList = [job for job in self.__jobList if type(job) == types.StringType or not job.finished]

	def submit(self, func, paramTuple, dependentFuncTuple = (), importTuple = (), callBack = None, callBackArgs = (), cacheFileName = None, maxNbProcess = None):
		if self._nbCpus == -1:
			if cacheFileName:
				if Utilities.doesFileExist(cacheFileName):
					res = Utilities.loadCache(cacheFileName)
				else:
					res = Utilities.getFunctionResultWithCache(cacheFileName, func, *paramTuple)
			else:
				res = func(*paramTuple)
			self.__jobList.append(res)
			if len(res) == 0:
				raise NotImplementedError
			return
		job = cacheFileName
		if cacheFileName is None or not Utilities.doesFileExist(cacheFileName):
			if maxNbProcess is None:
				job = self._jobServer.submit(func, paramTuple, dependentFuncTuple, importTuple,
								             callback = callBack, callbackargs = callBackArgs)
			else:
				job = self._jobServer.submit(func, paramTuple, dependentFuncTuple, importTuple)
			if cacheFileName:
				job._cacheFileName = cacheFileName
		self.__jobList.append(job)
		self.__waitForJobs()
		if maxNbProcess is not None and len(self.__jobList) > maxNbProcess:
			self.wait(callBack, *callBackArgs)
		return job
	
	def submitList(self, func, paramList, dependentFuncTuple, importTuple, step = None,
		           callBack = None, callBackArgs = ()):
		if step is None:
			step = len(paramList)
		resList = []
		for currentParamList in iterRange(paramList, step):
			for paramTuple in currentParamList:
				if type(paramTuple) != types.TupleType:
					paramTuple = (paramTuple, )
				resList.append(self.submit(func, paramTuple, dependentFuncTuple, importTuple,callBack, callBackArgs))
		return resList
	
	def submitJobAndGetId(self, command, memory = None, nbProc = 1, dependentJobId = None, jobName = None, machineToUseList = None, errorFile = None, outputFile = None, queue = None, arrayIdxStr = None, machineToExcludeList = None):
		if errorFile:
			command += ' 2> %s' % errorFile
		self.submit(T().run, (command, ))
	
	def removeJob(self, job):
		self.__jobList.remove(job)

	def __getResFromJob(self, job):
		if type(job) == types.StringType:
			res = Utilities.loadCache(job)
		else:
			if hasattr(job, '_cacheFileName') and job._cacheFileName:
				res = Utilities.getFunctionResultWithCache(job._cacheFileName, job)
			else:
				res = job()
		return res
	
	def waitForParallelJobs(self, callbackFunc = None, *args):
		if callbackFunc is None:
			#self._jobServer.wait()
			if self._nbCpus == -1:
				return
			while len(self.__jobList):
				job = self.__jobList.pop(0)
				self.__getResFromJob(job)
			return
		#print 'WAITING'
		while len(self.__jobList):
			job = self.__jobList.pop(0)
			res = job
			if self._nbCpus != -1:
				res = self.__getResFromJob(job)
			#print res
			if callbackFunc:
				#print 'CALLING'
				if res is None:
					raise NotImplementedError
				callbackFunc(res, *args)
			#print 'JOB DONE'

	wait = waitForParallelJobs
	waitUntilClusterIsFree = waitForParallelJobs


class HpcBase:
	JOB_ID_VAR = None
	TASK_ID_VAR = None
	MASTER_VAR = None

	_startJobIdStr = None
	_endJobIdStr = None
	_defaultMemory = 4
	_nbTries = 1
	_deleteJobCmd = None

	def __init__(self):
		self._jobList = []
		if self.__createJobList():
			self.__targetDir, self.__nbParts = os.environ.get('CREATE_JOBS').split(',')
			self.__nbParts = int(self.__nbParts)
		
	def _addOptionStrToCmd(self, cmd, optionList):
		for optionName, optionValue in optionList:
			cmd += ' %s %s' % (optionName, optionValue)
		return cmd
	
	def deleteJob(self, jobId):
		if type(jobId) == types.IntType:
			jobId = [jobId]
		if not self._deleteJobCmd:
			raise NotImplementedError('Delete cmd not set')
		Utilities.mySystem('%s %s' % (self._deleteJobCmd, ' '.join([str(jId) for jId in jobId])))
	
	def __createJobList(self):
		return os.environ.get('CREATE_JOBS')
		
	def __del__(self):
		if self.__createJobList() and self._jobList:
			import math
			Utilities.mySystem('mkdir -p %s' % self.__targetDir)
			nbJobs = int(math.ceil(1. * len(self._jobList) / self.__nbParts))
			print 'Creating %d job dump files for %d jobs with %d jobs per file in %s' % (self.__nbParts, len(self._jobList), nbJobs, self.__targetDir)
			for i, jobList in enumerate(iterRange(self._jobList, nbJobs)):
				dumpFile = os.path.join(self.__targetDir, '%d-%d.pyDump' % (i, self.__nbParts))
				Utilities.saveCache(jobList, dumpFile)
			
	def getNbRunningJobs(self):
		raise NotImplementedError
	
	def getJobIdList(self):
		raise NotImplementedError
	
	def getJobDetails(self, jobId):
		raise NotImplementedError
	
	def isMasterNode(self):
		#for key in os.environ:
			#if self.MASTER_VAR in key:
				#return True
		return os.environ.get('HOSTNAME', '') in ['Unicluster', 'master1']

	def areAllVarInEnv(self):
		for varName in [self.JOB_ID_VAR, self.TASK_ID_VAR]:
			if not os.environ.has_key(varName):
				return
		return True

	def _getJobStr(self, command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile = None, outputFile = None, queue = None, arrayIdxStr = None, email = None, walltime = 604800, node = 1, optionList = None):
		raise NotImplementedError
	
	def _extractJobIdFromStr(self, jobIdStr):
		return jobIdStr
	
	def _submitRawStrAndgetJobId(self, jobStr):
		from WebExtractor import WebExtractor
		fh = os.popen(jobStr)
		content = fh.read()
		for i in range(self._nbTries):
			try:
				jobIdStr = WebExtractor()._getStrIncludedInTag(content, self._startJobIdStr, self._endJobIdStr)
				jobIdStr = self._extractJobIdFromStr(jobIdStr)
				jobId = int(jobIdStr)
				break
			except:
				print 'Unhandled', [content]
				if i == self._nbTries-1:
					raise
				print 'Submission failed i = %d, retrying...' % i
		fh.close()
		return jobId
	
	def _getMachineList(self):
		raise NotImplementedError
	
	def submitJobAndGetId(self, command, memory = None, nbProc = 1, dependentJobId = None, jobName = None, machineToUseList = None, errorFile = None, outputFile = None, queue = None, arrayIdxStr = None, machineToExcludeList = None, email = None, walltime = 604800, node = 1, nextJobDict = None, scriptName = None, expectedOutputFile = None, optionList = None):
		if queue and ',' in queue:
			queue = tuple(queue.split(','))
		if not email:
			email = os.environ.get('CLUSTER_MAIL')
		if not machineToExcludeList:
			machineToExcludeList = os.environ.get('MACHINE_TO_EXCLUDE', '').split(',')
			if machineToExcludeList == ['']:
				machineToExcludeList = None
		if memory is None:
			memory = self._defaultMemory
		if machineToExcludeList:
			if machineToUseList:
				raise NotImplementedError
			print 'machineToExcludeList = ', machineToExcludeList
			machineToUseList = list(set(self._getMachineList()) - set(machineToExcludeList))
			machineToUseList.sort()
		print 'cmd = [%s]' % command
		if nextJobDict:
			dependentJobId = None
		if scriptName:
			from FileHandler import CsvFileWriter
			outFh = CsvFileWriter(scriptName)
			shaBang = '#!/bin/bash'
			if shaBang not in command:
				command = shaBang + '\n' + command
			outFh.write(command)
			outFh.close()
			Utilities.mySystem('chmod +x %s' % scriptName)
			command = scriptName
		if self.__createJobList():
			if not expectedOutputFile:
				raise NotImplementedError('expectedOutputFile is None for command: %s' % command)
			self._jobList.append((command, expectedOutputFile))
			return
		jobStr = self._getJobStr(command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile, outputFile, queue, arrayIdxStr, email, walltime, node, optionList)
		print 'cmd = [%s]' % jobStr
		if type(jobStr) == types.StringType:
			jobList = [jobStr]
		else:
			jobList = jobStr
		currentJobIdList = []
		if nextJobDict and len(jobList) > 1:
			raise NotImplementedError("jobList's length should be <= 1 when nextJobDict is not null: %s %s" % (jobList, nextJobDict))
		for jobStr in jobList:
			#print 'job = [%s]' % jobStr
			jobId = self._submitRawStrAndgetJobId(jobStr)
			print 'Submitted with jobId %d' % jobId
			self._jobList.append(jobId)
			currentJobIdList.append(jobId)
		if len(currentJobIdList) == 1:
			currentJobIdList = currentJobIdList[0]
		return currentJobIdList

	def submitJobs(self, commandAndParamList):
		jobId = None
		for commandAndParams in commandAndParamList:
			memory = 4
			nbProc = 1
			jobName = None
			if len(commandAndParams) == 2:
				command, memory = commandAndParams
			elif len(commandAndParams) == 3:
				if type(commandAndParams[-1]) == types.IntType:
					command, memory, nbProc = commandAndParams
				else:
					command, memory, jobName = commandAndParams
			elif len(commandAndParams) == 4:
				command, memory, nbProc, jobName = commandAndParams
			elif len(commandAndParams) == 5:
				command, memory, nbProc, jobName, jobId = commandAndParams
			paramList = [command, memory, nbProc, jobId, jobName]
			jobId = self.submitJobAndGetId(*paramList)

	def getTaskIdx(self):
		try:
			return int(os.environ.get(self.TASK_ID_VAR, 0)) - 1
		except:
			return

	def getJobId(self):
		return int(os.environ.get(self.JOB_ID_VAR, 0))

	def _isJobFinished(self, jobId):
		raise NotImplementedError
	
	def waitUntilClusterIsFree(self):
		while True:
			nbJobs = self.getNbRunningJobs()
			if not nbJobs:
				break
			print 'Waiting %ds' % nbJobs
			time.sleep(nbJobs)
	
	def wait(self):
		while self._jobList:
			jobId = self._jobList.pop()
			if not self._isJobFinished(jobId):
				self._jobList.append(jobId)

	def waitForJob(self, jobId, sleepTime = 1):
		while True:
			time.sleep(sleepTime)
			if self._isJobFinished(jobId):
				break


class SGEcluster(HpcBase):
	JOB_ID_VAR = 'JOB_ID'
	TASK_ID_VAR = 'SGE_TASK_ID'
	MASTER_VAR = 'SGE'

	_startJobIdStr = 'Your job'
	_endJobIdStr = ' ('
	_deleteJobCmd = 'qdel'
	
	def getJobIdList(self):
		fh = os.popen('qstat')
		jobIdList = []
		fh.readline()
		line = fh.readline()
		if  '---------------------------------' not in line:
			raise NotImplementedError('"---------------------------------" expected in line "%s"' % line)
		for line in fh:
			splittedLine = line.split()
			status = splittedLine[4]
			machine = splittedLine[7]
			jobIdList.append((int(splittedLine[0]), status, machine))
		return jobIdList
	
	def getJobDetails(self, jobId):
		fh = os.popen('qstat -j %d' % jobId)
		return fh.read()
	
	def _getMachineList(self):
		return ['c1l%d' % i for i in range(1, 17)]
	
	def _isJobFinished(self, jobId):
		fh = os.popen('qstat')
		content = fh.read()
		fh.close()
		print 'Content = [%s]' % content
		return ' %d ' % jobId  not in content
	
	def _extractJobIdFromStr(self, jobIdStr):
		if not ValueParser.isNb(jobIdStr):
			jobIdStr = jobIdStr.split()[-1].split('.')[0]
		return int(jobIdStr)
	
	def _getJobStr(self, command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile = None, outputFile = None, queue = None, arrayIdxStr = None, email = None, walltime = None, node = None, optionList = None):
		if nbProc > 1:
			memory = max(memory, nbProc * 3)
		cmd = 'echo "%s" | qsub -l h_vmem=%dG,virtual_free=%dG ' % (command, memory, memory)
		if dependentJobId:
			if type(dependentJobId) != types.ListType:
				dependentJobId = [dependentJobId]
			cmd += '-hold_jid %s ' % (','.join([str(jobId) for jobId in dependentJobId]))
		if jobName:
			switch = 'N'
			if ValueParser.isNb(jobName.split('-')[0]):
				switch = 't'
			cmd += '-%s %s ' % (switch, jobName)
		if errorFile:
			cmd += '-e %s ' % errorFile
		if outputFile:
			cmd += '-o %s ' % outputFile
		if jobName and '[' in jobName:
			arrayIdxStr = jobName.split('[')[-1].split(']')[0]
			cmd += '-t %s ' % arrayIdxStr
		if machineToUseList:
			cmd += '-q %s ' % (','.join(['*@%s' % machineName for machineName in machineToUseList]))
		if email:
			cmd += '-m eas -M %s ' % email
		#if nbProc > 1:
			#cmd += '-pe make %d ' % nbProc
		print 'JOB -> %s' % cmd
		return cmd


class LSFcluster(HpcBase):
	JOB_ID_VAR = 'LSB_JOBID'
	TASK_ID_VAR = 'LSB_JOBINDEX'
	MASTER_VAR = 'LSF'

	_startJobIdStr = '<'
	_endJobIdStr = '>'

	def _isJobFinished(self, jobId):
		fh = os.popen('bjobs')
		content = fh.read()
		fh.close()
		#print 'Content = [%s]' % content
		return '%d ' % jobId not in content
	
	def _getJobStr(self, command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile = None, outputFile = None, queue = None, arrayIdxStr = None, email = None, walltime = None, node = None, optionList = None):
		cmd = 'bsub -n %d -R "rusage[mem=%d]" -M %d ' % (nbProc, memory, memory)
		if jobName:
			cmd += '-J "%s" ' % jobName
		if dependentJobId:
			if type(dependentJobId) != types.ListType:
				dependentJobId = [dependentJobId]
			cmd += '-w "%s" ' % (' && '.join([str(jobId) for jobId in dependentJobId]))
		if machineToUseList:
			cmd += '-m "%s" ' % (' '.join(machineToUseList))
		if errorFile:
			cmd += '-e %s ' % errorFile
		if outputFile:
			cmd += '-o %s ' % outputFile
		return cmd + '"%s"' % command


class MOABcluster(HpcBase):
	JOB_ID_VAR = 'LSB_JOBID'
	TASK_ID_VAR = 'LSB_JOBINDEX'
	MASTER_VAR = 'PBS'
	
	_startJobIdStr = '\n'
	_endJobIdStr = '\n'
	_defaultMemory = 3
	_nbTries = 10
	
	def getNbRunningJobs(self):
		fh = os.popen('showq -u $USER')
		nbJobs = fh.readlines()[-2].strip().split()[-1]
		return int(nbJobs)
	
	def _isJobFinished(self, jobId):
		fh = os.popen('checkjob -v %d' % jobId)
		content = fh.read()
		fh.close()
		return 'State: Completed' in content
	
	def __getJobStr(self, command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile = None, outputFile = None, queue = None, arrayIdxStr = None, email = None, walltime = 604800, node = 1, optionList = None):
		partList = command.split()
		if len(partList) > 1 and partList[0].split('.')[-1] == 'sh':
			fh = open(partList[0])
			command = fh.read()
			fh.close()
			if '$*' not in command:
				raise NotImplementedError('Expected to find "$*" in %s but found "%s"' % (partList[0], command))
			command = command[:command.rfind('$*')]
			command += ' ' + ' '.join(partList[1:])
		#print 'CMD [%s]' % command
		dependentStr = ''
		if dependentJobId:
			if type(dependentJobId) != types.ListType:
				dependentJobId = [dependentJobId]
			dependentStr = ',depend=%s' % (':'.join([str(jobId) for jobId in dependentJobId]))
		memoryStr = ''
		if memory != self._defaultMemory:
			memoryStr = ',pmem=%dgb' % memory
		cmd = 'echo "%s" | msub -l nodes=%d:ppn=%d%s,walltime=%d%s ' % (command, node, nbProc, memoryStr, walltime, dependentStr)
		if not queue:
			queue = 'sw'
		if queue:
			cmd += '-q %s ' % queue
		print '==cmd = [%s]==' % cmd
		if jobName:
			cmd += '-N %s ' % jobName
		if errorFile:
			cmd += '-e %s ' % errorFile
		if outputFile:
			cmd += '-o %s ' % outputFile
		return cmd
	
	def _getJobStr(self, command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile = None, outputFile = None, queue = 'sw', arrayIdxStr = None, email = None, walltime = 604800, node = 1, optionList = None):
		idxList = None
		if jobName and '[' in jobName and ']' in jobName:
			idxStr = jobName.split('[')[-1].split(']')[0]
			idxList = ValueParser().getIdxListFromIntervalStrOrList(idxStr)
		cmd = self.__getJobStr(command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile, outputFile, queue, arrayIdxStr, email, walltime, node, optionList)
		if idxList:
			cmdList = []
			keyword = 'echo "'
			partList = cmd.split(keyword)
			cmd = partList[0] + keyword + '%%s;%s' % partList[1]
			if '[' not in jobName or ']' not in jobName:
				raise NotImplementedError('Expected to find "[" and "]" in jobName "%s"' % jobName)
			toReplace = '[' + jobName.split('[')[1].split(']')[0] + ']'
			#print 'To Repl ]%s[' % toReplace
			for idx in idxList:
				cmd2 = cmd % 'export %s=%d' % (self.TASK_ID_VAR, idx)
				cmd2 = cmd2.replace('-N %s ' % jobName, '-N %s ' % (jobName.replace(toReplace, '[%d]' % idx)))
				cmdList.append(cmd2)
			#print '>>>>>>>>>>'
			#print cmdList
			#print '<<<<<<<<<<'
			return cmdList
		return cmd

	
class SLURMcluster(HpcBase):
	JOB_ID_VAR = 'SLURM_ARRAY_JOB_ID'
	TASK_ID_VAR = 'SLURM_ARRAY_TASK_ID'

	_startJobIdStr = 'Submitted batch job '
	_endJobIdStr = '\n'
	_defaultMemory = 4
	_deleteJobCmd = 'scancel'
	
	def getJobDetails(self, jobId):
		fh = os.popen('sjinfo %d' % jobId)
		return fh.read()
	
	def getJobIdList(self):
		for jobStr in os.popen('squeue -u %s' % os.environ['USER']):
			partList = jobStr.split()
			if partList[0] == 'JOBID':
				continue
			yield int(partList[0]), partList[4], partList[-1]
	
	def _getMachineList(self):
		return ['node%02d' % i for i in range(19, 23) + range(1, 7)]
	
	def __getScriptNameForJob(self, command, scriptName = None):
		if not scriptName:
			import random
			random.seed(time.time())
			scriptName = str(random.random()) + '.sh'
			scriptName = '/env/cng/proj/projet_LIVER_356/scratch/src/victor/jobs/%s' % scriptName
		fh = open(scriptName, 'w')
		fh.write('#!/bin/bash\n%s' % command)
		fh.close()
		Utilities.mySystem('chmod +x %s' % scriptName)
		return scriptName
	
	def _getJobStr(self, command, memory, nbProc, dependentJobId, jobName, machineToUseList, errorFile = None, outputFile = None, queue = 'sw', arrayIdxStr = None, email = None, walltime = 86400, node = 1, optionList = None):
		if not walltime:
			walltime = 86400
		adjustMemory = True
		if _guessLocation() == HpcScriptBase.CEPH_SLURM:
			#print 'SOKO -> [%s]' % command
			fullCmd = command
			if os.path.isfile(fullCmd):
				fullCmd = open(command).read()
			if ' -p shell -f ' in fullCmd:
				fullCmd = open(fullCmd.split(' -p shell -f ')[1].split()[0]).read()
				#print 'SOKO2 -> [%s]' % fullCmd
			if 'GenomeAnalysisTK' in fullCmd or 'java' in fullCmd:
				adjustMemory = False
				currentMemory = os.environ.get('MEMORY')
				if currentMemory:
					memory = int(currentMemory)
				factor = 4
				currentFactor = os.environ.get('FACTOR')
				if currentFactor:
					factor = int(currentFactor)
				nbProc *= factor
				#nbProc = min(_detect_ncpus(), nbProc)
				if memory > 10:
					memory /= 2
			walltime *= 4
		if adjustMemory and nbProc:
			memory /= nbProc
		scriptName = self.__getScriptNameForJob(command, FileNameGetter(errorFile).get('_cmd.sh'))
		#scriptName = FileNameGetter(errorFile).get('_cmd.sh')
		cmd = 'sbatch -c %d --mem-per-cpu=%d ' % (nbProc, memory * 1000)
		qos = None
		if type(queue) == types.TupleType:
			queue, qos = queue
		if jobName:
			cmd += '-J %s ' % jobName
		if errorFile:
			cmd += '-e %s ' % errorFile
		if outputFile:
			cmd += '-o %s ' % outputFile
		if queue:
			qName = queue
			if type(queue) == types.ListType:
				qName, nb = queue[0]
				nb -= 1
				if not nb:
					queue.pop(0)
				else:
					queue[0] = qName, nb
			cmd += '-p %s ' % qName
		if qos:
			cmd += '--qos %s ' % qos
		if email:
			cmd += '--mail-type=END '
		if walltime:
			cmd += '-t %d ' % (walltime / 60)
		if dependentJobId:
			if type(dependentJobId) != types.ListType:
				dependentJobId = [dependentJobId]
			else:
				dependentJobId = [jobId for jobId in dependentJobId if jobId]
			if dependentJobId:
				cmd += '-d afterok:%s ' % (':'.join([str(jobId) for jobId in dependentJobId]))
		if machineToUseList:
			cmd += '-w %s ' % (','.join(machineToUseList))
		if optionList:
			cmd = self._addOptionStrToCmd(cmd, optionList)
		cmd += ' %s' % scriptName
		print [cmd]
		return cmd
	

def guessHpc(allowLocalRun = False, nbCpus = None, useFromMasterNode = True, *args):
	if not nbCpus:
		nbCpus = _getNbAvailableCpus()
	if os.environ.get('USE_CLUSTER') == '0':
		print msg
		return ThreadManager(nbCpus, *args)
	if os.environ.has_key('TORTUGA_ROOT'):
		return LSFcluster()
	for clusterClass in [SGEcluster, LSFcluster]:
		cluster = clusterClass()
		if cluster.areAllVarInEnv():
			if isinstance(cluster, SGEcluster) and useFromMasterNode and not __isMasterNode():
				cluster = ThreadManager(nbCpus, *args)
			return cluster
	for envName in os.environ:
		if envName[:4] == 'SGE_':
			if useFromMasterNode and not __isMasterNode():
				return ThreadManager(nbCpus, *args)
			return SGEcluster()
		elif envName[:4]== 'LSF_':
			return LSFcluster()
		elif envName[:4] == 'PBS_' or envName == 'MODULEPATH':
			return MOABcluster()
	if allowLocalRun:
		print msg
		return ThreadManager(nbCpus, *args)
	

class HpcScriptBase:
	_idx = None
	_jobId = None

	CNG = 0
	EBI = 1
	CEPH = 2
	CNG_MAC = 3
	MC_GILL = 4
	GUILLIMIN = 5
	CNG_SLURM = 6
	CCRT_SLURM = 7
	CEPH_SLURM = 8

	def __init__(self, nbCpus = 30):
		self._nbCpus = nbCpus
		self._cluster = guessHpc()
		self._location = _guessLocation()
		if self._cluster and not isinstance(self._cluster, ThreadManager):
			self._idx = self._cluster.getTaskIdx()
			self._jobId = self._cluster.getJobId()
		if self._location in [self.CNG, self.CEPH]:
			self._clusterClass = SGEcluster
		else:
			self._clusterClass = LSFcluster

	def _getParamList(self, *args):
		raise NotImplementedError

	def _processAll(self, *args):
		threadManager = ThreadManager(self._nbCpus)
		for param in self._getParamList(*args[:-1]):
			args = list(args[:-1]) + [param]
			print args
			threadManager.submit(self._action, *args)
		threadManager.wait()

	def process(self, *args):
		if not self._cluster:
			self._processAll(*args)
		else:
			self._process(*args)

	def _process(self, *args):
		paramList = self._getParamList(*args[:-1])
		idx = args[-1]
		print 'IDX', idx, args
		if idx is not None:
			self._idx = idx
		param = paramList[self._idx]
		args = list(args[:-1]) + [param]
		self._action(*args)

	def _action(self, *args):
		raise NotImplementedError


def run(options, args):
	cluster = ThreadManager(4)
	cluster.submitJobAndGetId('sleep 4 && touch job1_done', nbProc = 4)
	cluster.submitJobAndGetId('touch job2_done', nbProc = 1)
	
runFromTerminal(__name__, [], run)
