import threading
import time
import sys
import os
import datetime
import shutil
import hashlib

run_flag = True 

def log(file, content):
    """Logs both to console and to file

        Parameter:
            file (string): path to the logging file
            content (string): content to be logged
    """
    curated_content = f"{datetime.datetime.now()}: {content}" 
    print(f"{threading.current_thread().name} : {curated_content}")
    f = open(file, "a")
    f.write(curated_content+"\n")
    f.close


def file_to_md5(file, blocksize=2**20):
    """Calculate MD5 hash for a specific file

        Parameter:
            file (string): path to the file
            blocksize (int): size read per hashed block, powers of 2
    """
    m = hashlib.md5()
    with open( file , "rb" ) as f:
        while True:
            buffer = f.read(blocksize)
            if not buffer:
                break
            m.update( buffer )
    return m.hexdigest()


def copy_file(logfile, source_path, copy_path):
    """Copies file and logs the opperation

        Parameter:
            logfile (string): path to the logging file
            source_path (string): path to the file to be copied
            copy_path (string): path to where the file should be copied into
    """
    shutil.copyfile(source_path,copy_path)
    log(logfile, f"copied {source_path} to {copy_path}")


def remove_dir(logfile, path):
    """Removes the Directory and logs the opperation

        Parameter:
            logfile (string): path to the logging file
            path (string): path to the directory to be removed
    """
    os.removedirs(path)
    log(logfile, f"removed {path}")


def cached_remove_file(logfile, path, cache):
    """Removes the file, clears it from the cache
        and logs the opperation

        Parameter:
            logfile (string): path to the logging file
            path (string): path to the file to be removed
            cache (dictionary): cache dictionary belonging to the parent thread
    """
    os.remove(path)
    log(logfile, f"removed {path}")
    if path in cache["md5cache"]:
        del cache["md5cache"][path]


def cached_compare_and_copy(logfile,source_path,copy_path,cache):
    """Compares two files, if they differ, replace the replica file
        checks cache for replica md5 sums before calculating them, and updates
        the cache acordingly after replacement

        Parameter:
            logfile (string): path to the logging file
            source_path (string): path to the file to be copied
            copy_path (string): path to where the file should be copied into
            cache (dictionary): cache dictionary belonging to the parent thread
    """
    if copy_path in cache["md5cache"]:
        copy_md5 = cache["md5cache"][copy_path]
    else:
        copy_md5 = file_to_md5(copy_path)
        cache["md5cache"][copy_path] = copy_md5
    sorce_md5 = file_to_md5(source_path)
    if sorce_md5 != copy_md5:
        cached_remove_file(logfile,copy_path, cache)
        copy_file(logfile,source_path,copy_path)
        cache["md5cache"][copy_path] = sorce_md5


def one_way_sync(args, lock, cache):
    """One-Way synchronization operation between two folders

        Parameter:
            args [source,destination,logfile] (array):
                -source (string): path to the file to be copied
                -destination (string): path to where the file should be copied into
                -logfile (string): path to the logging file
            lock (lock): locks the current controller thread,
                            makes it impossible to start a new sync before
                            the last one finishes
            cache (dictionary): cache dictionary belonging to the parent thread
    """
    if lock.locked():
       print(f"Aborting request ({args[0]}->{args[1]}), sync already underway")
       return
    lock.acquire()
    source,destination,logfile = args
    print(f"({args[0]}->{args[1]}): sync started")
    if destination[-1] != '\\':
        destination += '\\'
    seen_paths = []
    for root, dirs, files in os.walk(source, topdown=True):
        new_root = os.path.join(destination,root.replace(source,'')[1:])
        for name in dirs:
            copy_path = os.path.join(new_root,name)
            seen_paths.append(copy_path)
            if not os.path.isdir(copy_path):
                try:
                    os.makedirs(copy_path)
                except Exception as e:
                    raise e
                log(logfile,f"Created folder {copy_path}")
        for name in files:
            source_path = os.path.join(root, name)
            copy_path = os.path.join(new_root,name)
            seen_paths.append(copy_path)
            if not os.path.isfile(copy_path):
                copy_file(logfile,source_path,copy_path)
            else:
                cached_compare_and_copy(logfile,source_path,copy_path,cache)
    for root, dirs, files in os.walk(destination, topdown=True):
        for name in dirs:
            copy_path = os.path.join(root, name)
            if copy_path not in seen_paths:
                remove_dir(logfile,copy_path)
        for name in files:
            copy_path = os.path.join(root, name)
            if copy_path not in seen_paths:
                cached_remove_file(logfile,copy_path,cache)
    lock.release()


def validateArgs(args):
    """Validates arguments needed for synchronization

        Parameter:
            args [source,destination,interval,logfile] (array):
                -source (string): path to the file to be copied
                -destination (string): path to where the file should be copied into
                -interval (int): interval between scans
                -logfile (string): path to the logging file
    """
    if len(args) < 4:
        print("Not enough arguments passed")
        print("Arguments required: #source #destination #interval_in_sec #log_file")
        print("usage example> python PyFolderSync.py \"./dev\" \"./devcopy\" 1 log.txt")
        return False
    source,destination,interval,logfile = args
    if not os.path.isdir(source):
        print("Source folder not found")
        return False
    try:
        interval = int(interval)
    except ValueError:
        print(f"interval must be an integer, value received:{interval}")
        return False
    if not os.path.isfile(logfile):
        try:
            log(logfile, "Log File Created")
        except OSError as g:
            print("invalid log directory/path")
            return False
        except Exception as e:
            raise e
    if not os.path.exists(destination):
        os.makedirs(destination)
        log(logfile,f"Created Folder {destination}")
    return [source,destination,interval,logfile]

def sync_thread_controller(strategy, args):#strategy, [#source #destination #interval_in_ms #log]
    """Controller responsible for creating sync thread jobs every interval

        Parameter:
            strategy (function): job function that will be executed (one-way/two-way)
            args [source,destination,interval,logfile] (array):
                -source (string): path to the file to be copied
                -destination (string): path to where the file should be copied into
                -interval (int): interval between scans
                -logfile (string): path to the logging file
    """
    global run_flag
    interval = args.pop(2)
    lock = threading.Lock()
    controler_cache = {"md5cache": {}}
    spawned_thread_name = f"({args[0]}{"<" if strategy != one_way_sync else ""}->{args[1]}) running sync"
    while run_flag:
        threading.Thread(target=strategy, args=[args, lock, controler_cache],
                         name=spawned_thread_name).start()
        time.sleep(interval)
    print(f"{threading.current_thread().name} thread closing")
    return

def one_way_controller_spawner(args):
    """Creates controllers for One-way synchronization

        Parameter:
            args [source,destination,interval,logfile] (array):
                -source (string): path to the file to be copied
                -destination (string): path to where the file should be copied into
                -interval (int): interval between scans
                -logfile (string): path to the logging file
    """
    args = validateArgs(args)
    if not args:
        print("Aborting Thread creation")
        return
    threading.Thread(target=sync_thread_controller, args=[one_way_sync, args],
                      name= f"({args[0]}->{args[1]}) controller").start()
    return

def main():
    global run_flag
    one_way_controller_spawner(sys.argv[1:])
    while threading.active_count() > 1:
        print ("""
----------------------------------------
        1.Thread information
        2.Create new Sync Thread
        3.Safe exit
----------------------------------------
        """)
        choice = input("What would you like to do?\n") 
        if choice=="1": 
            print("------ Threads -------")
            for thread in threading.enumerate(): 
                print("--",thread.name)
            print("---------------------\n")
        elif choice=="2":
            new_args = input("type: source destination interval logfile\n").split()
            one_way_controller_spawner(new_args)
        elif choice=="3":
            run_flag = False
            return
        elif choice !="":
            print("\n Not Valid Choice Try again")
    
    

if __name__ == "__main__":
    main()


